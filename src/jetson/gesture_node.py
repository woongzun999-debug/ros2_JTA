#!/usr/bin/env python3
"""
[Tier 2 Jetson Orin Nano] ArUco 숫자 카드 + 양손 Nav2 제어 하이브리드 노드
- /camera/image_raw/compressed 구독 (depth=1, Zero-Lag 프레임 드롭 가드)
- 1) OpenCV ArUco (DICT_4X4_50, 매 프레임 ~5ms CPU):
     * ID 0      -> {"cmd": "ESTOP"} (0.25s 유지, 최우선)
     * ID 1 ~ 10 -> {"cmd": "GOTO_WP", "wp_index": 1..10} (0.8s 유지)
- 2) MediaPipe Hands (2프레임당 1회 ~7.5Hz CPU, ArUco 미감지 시):
     * ✋ 한 손 펼치기  -> {"cmd": "NAV2_STOP"}  (1.5s 유지, 쿨다운 10s)
     * 👍 한 손 엄지척  -> {"cmd": "NAV2_START"} (2.0s 유지, 쿨다운 15s)
"""

import json
import math
import time

import cv2
import mediapipe as mp
import numpy as np

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from sensor_msgs.msg import CompressedImage


def _dist2d(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def classify_hand_pose(landmarks) -> str:
    """단일 손 랜드마크에서 OPEN_PALM(손바닥/손등 펼침) / THUMBS_UP(엄지척) / OTHER 판정."""
    lm = landmarks.landmark
    wrist = lm[0]

    extended_4 = 0
    curled_4 = 0
    for tip_idx, pip_idx in ((8, 6), (12, 10), (16, 14), (20, 18)):
        d_tip = _dist2d(wrist, lm[tip_idx])
        d_pip = _dist2d(wrist, lm[pip_idx])
        if d_tip > d_pip * 1.15:
            extended_4 += 1
        elif d_tip <= d_pip * 1.25:
            curled_4 += 1

    # 1) ✋ 활짝 편 손 (앞면/뒷면 무관, 검지~새끼 4개 모두 펴짐)
    if extended_4 == 4:
        return 'OPEN_PALM'

    # 2) 👍 엄지척 (앞면/뒷면/측면 무관, 검지~새끼 중 3개 이상 접힘 + 엄지 위로)
    thumb_tip = lm[4]
    thumb_ip = lm[3]
    thumb_mcp = lm[2]
    index_mcp = lm[5]

    thumb_pointing_up = (
        thumb_tip.y < thumb_ip.y - 0.015
        and thumb_tip.y < thumb_mcp.y - 0.025
        and thumb_tip.y < index_mcp.y - 0.015
    )
    if curled_4 >= 3 and thumb_pointing_up:
        return 'THUMBS_UP'

    return 'OTHER'


class JetsonHybridCmdNode(Node):
    def __init__(self):
        super().__init__('jetson_gesture_node')

        self.declare_parameter('wp_hold_sec', 0.8)
        self.declare_parameter('estop_hold_sec', 0.25)
        self.declare_parameter('nav2_stop_hold_sec', 1.5)
        self.declare_parameter('nav2_start_hold_sec', 2.0)
        self.declare_parameter('wp_cooldown_sec', 4.0)
        self.declare_parameter('estop_cooldown_sec', 2.0)
        self.declare_parameter('nav2_stop_cooldown_sec', 10.0)
        self.declare_parameter('nav2_start_cooldown_sec', 15.0)
        self.declare_parameter('mp_frame_stride', 2)

        self.wp_hold_sec = float(self.get_parameter('wp_hold_sec').value)
        self.estop_hold_sec = float(self.get_parameter('estop_hold_sec').value)
        self.nav2_stop_hold_sec = float(self.get_parameter('nav2_stop_hold_sec').value)
        self.nav2_start_hold_sec = float(self.get_parameter('nav2_start_hold_sec').value)
        self.wp_cooldown_sec = float(self.get_parameter('wp_cooldown_sec').value)
        self.estop_cooldown_sec = float(self.get_parameter('estop_cooldown_sec').value)
        self.nav2_stop_cooldown_sec = float(self.get_parameter('nav2_stop_cooldown_sec').value)
        self.nav2_start_cooldown_sec = float(self.get_parameter('nav2_start_cooldown_sec').value)
        self.mp_frame_stride = max(1, int(self.get_parameter('mp_frame_stride').value))

        self.processing = False
        self.frame_count = 0

        # 디바운싱 / 유지 시간 추적 상태
        self.candidate_key: tuple[str, int] | None = None
        self.candidate_since: float = 0.0
        self.candidate_last_seen: float = 0.0
        self.last_cmd_times: dict[str, float] = {}
        self.last_state_pub: float = 0.0

        # 1) OpenCV ArUco DICT_4X4_50
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        aruco_params = cv2.aruco.DetectorParameters()
        self.detector = cv2.aruco.ArucoDetector(aruco_dict, aruco_params)

        # 2) MediaPipe Hands (한 손 펼치기 / 한 손 엄지척 전용, 정밀 모델 complexity=1)
        self.hands = mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            model_complexity=1,
            min_detection_confidence=0.45,
            min_tracking_confidence=0.45,
        )

        # 영상 구독 (depth=1, Zero-Lag 규약)
        self.cam_sub = self.create_subscription(
            CompressedImage,
            '/camera/image_raw/compressed',
            self.camera_callback,
            1,
        )

        self.cmd_pub = self.create_publisher(String, '/gesture/cmd', 10)
        self.state_pub = self.create_publisher(String, '/gesture/state', 10)

        self.get_logger().info(
            f'>>> [Hybrid Cmd Node] ArUco(0=ESTOP, 1..10=WP) + 1-Hand(OPEN_PALM=NAV2_STOP {self.nav2_stop_hold_sec}s, '
            f'THUMBS_UP=NAV2_START {self.nav2_start_hold_sec}s) ready.'
        )

    def _reset_candidate(self):
        self.candidate_key = None
        self.candidate_since = 0.0

    def _select_aruco_cmd(self, corners, ids) -> tuple[str, int] | None:
        if ids is None or len(ids) == 0:
            return None

        best_id = None
        best_area = -1.0
        for corner, raw_id in zip(corners, ids.flatten()):
            mid = int(raw_id)
            if mid == 0:
                return ('ESTOP', 0)
            if 1 <= mid <= 10:
                area = float(cv2.contourArea(corner[0].astype(np.float32)))
                if area > best_area:
                    best_area = area
                    best_id = mid
        if best_id is not None:
            return ('GOTO_WP', best_id)
        return None

    def _detect_hand_nav2_cmd(self, frame_bgr) -> tuple[str, int] | None:
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = self.hands.process(frame_rgb)
        if not result.multi_hand_landmarks:
            return None
        pose = classify_hand_pose(result.multi_hand_landmarks[0])
        if pose == 'OPEN_PALM':
            return ('NAV2_STOP', 0)
        if pose == 'THUMBS_UP':
            return ('NAV2_START', 0)
        return None

    def _cmd_timing(self, cmd_type: str) -> tuple[float, float]:
        if cmd_type == 'ESTOP':
            return self.estop_hold_sec, self.estop_cooldown_sec
        if cmd_type == 'GOTO_WP':
            return self.wp_hold_sec, self.wp_cooldown_sec
        if cmd_type == 'NAV2_STOP':
            return self.nav2_stop_hold_sec, self.nav2_stop_cooldown_sec
        if cmd_type == 'NAV2_START':
            return self.nav2_start_hold_sec, self.nav2_start_cooldown_sec
        return 1.0, 4.0

    def camera_callback(self, msg: CompressedImage):
        # Zero-Lag 프레임 드롭 가드
        if self.processing:
            return
        self.processing = True
        try:
            np_arr = np.frombuffer(msg.data, dtype=np.uint8)
            frame_bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if frame_bgr is None:
                return

            self.frame_count += 1
            now = time.monotonic()

            # 1) ArUco 매 프레임 최우선 탐지 (~5ms)
            frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            corners, ids, _ = self.detector.detectMarkers(frame_gray)
            cmd_key = self._select_aruco_cmd(corners, ids)

            # 2) ArUco 미감지 시 2프레임당 1회(~7.5Hz) 한 손 주먹/엄지척 탐지
            ran_mp = False
            if cmd_key is None and (self.frame_count % self.mp_frame_stride == 0):
                ran_mp = True
                cmd_key = self._detect_hand_nav2_cmd(frame_bgr)

            if cmd_key is None:
                # 양손 후보가 유지 중이고 이번 프레임이 MediaPipe 스킵 프레임이면 유지
                if (
                    self.candidate_key is not None
                    and self.candidate_key[0] in ('NAV2_STOP', 'NAV2_START')
                    and not ran_mp
                ):
                    return
                timeout = 0.55 if (self.candidate_key and self.candidate_key[0].startswith('NAV2_')) else 0.35
                if self.candidate_key is not None and (now - self.candidate_last_seen) > timeout:
                    self._reset_candidate()
                return

            if cmd_key != self.candidate_key:
                self.candidate_key = cmd_key
                self.candidate_since = now
            self.candidate_last_seen = now
            held = now - self.candidate_since

            cmd_type, wp_idx = cmd_key
            req_hold, cooldown = self._cmd_timing(cmd_type)

            # 저주기 상태 텔레메트리 발행 (0.15s 간격)
            if now - self.last_state_pub >= 0.15:
                self.last_state_pub = now
                st_msg = String()
                st_msg.data = json.dumps({
                    'marker_id': wp_idx if cmd_type in ('ESTOP', 'GOTO_WP') else -1,
                    'candidate': cmd_type,
                    'wp_index': wp_idx,
                    'hold_sec': round(held, 2),
                    'req_hold_sec': req_hold,
                }, ensure_ascii=False)
                self.state_pub.publish(st_msg)

            last_t = self.last_cmd_times.get(cmd_type, 0.0)
            if held >= req_hold and (now - last_t) >= cooldown:
                self.last_cmd_times[cmd_type] = now
                self._reset_candidate()

                payload = {
                    'cmd': cmd_type,
                    'wp_index': wp_idx,
                    'marker_id': wp_idx if cmd_type in ('ESTOP', 'GOTO_WP') else -1,
                    'ts': round(time.time(), 3),
                }
                out = String()
                out.data = json.dumps(payload, ensure_ascii=False)
                self.cmd_pub.publish(out)
                self.get_logger().info(
                    f'✅ [Cmd Confirmed] {cmd_type} (wp={wp_idx})'
                )
        except Exception as exc:
            self.get_logger().error(f'Hybrid cmd frame error: {exc}')
        finally:
            self.processing = False

    def close(self):
        try:
            self.hands.close()
        except Exception:
            pass


def main(args=None):
    rclpy.init(args=args)
    node = JetsonHybridCmdNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()

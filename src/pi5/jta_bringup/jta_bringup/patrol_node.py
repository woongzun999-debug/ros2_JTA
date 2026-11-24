#!/usr/bin/env python3
"""Nav2 다중 웨이포인트 무한 루프 순찰(Patrol) 노드."""

import json
import math
import os
import signal
import time
import yaml

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
try:
    from rclpy.signals import SignalHandlerOptions
except ImportError:
    SignalHandlerOptions = None
from action_msgs.msg import GoalStatus
from ament_index_python.packages import get_package_share_directory
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, Twist
from nav2_msgs.action import NavigateToPose
from sensor_msgs.msg import BatteryState, LaserScan
from std_msgs.msg import Bool, String
from std_srvs.srv import SetBool


def yaw_to_quaternion(yaw: float) -> tuple[float, float]:
    """평면 요(yaw, rad)를 쿼터니언 (z, w)로 변환."""
    half = yaw * 0.5
    return math.sin(half), math.cos(half)


class PatrolNode(Node):
    """지정된 웨이포인트를 무한 순환하며 실패 지점을 스킵·방어하는 순찰 노드."""

    def __init__(self):
        super().__init__('jta_patrol_node')

        default_wp = os.path.join(
            get_package_share_directory('jta_bringup'),
            'config',
            'patrol_waypoints.yaml',
        )
        self.declare_parameter('waypoints_file', default_wp)
        self.declare_parameter('auto_start', False)
        self.declare_parameter('dwell_sec', 2.0)
        self.declare_parameter('retry_limit', 1)
        self.declare_parameter('follow_person_enabled', True)
        self.declare_parameter('follow_duration_sec', 30.0)
        self.declare_parameter('follow_cooldown_sec', 50.0)
        self.declare_parameter('follow_stop_dist', 0.65)
        self.declare_parameter('low_volt_threshold', 10.5)
        self.declare_parameter('low_volt_min_valid', 9.0)
        self.declare_parameter('low_volt_hold_sec', 5.0)

        self.waypoints_file = str(self.get_parameter('waypoints_file').value)
        self.dwell_sec = float(self.get_parameter('dwell_sec').value)
        self.retry_limit = int(self.get_parameter('retry_limit').value)
        self.follow_person_enabled = bool(self.get_parameter('follow_person_enabled').value)
        self.follow_duration_sec = float(self.get_parameter('follow_duration_sec').value)
        self.follow_cooldown_sec = float(self.get_parameter('follow_cooldown_sec').value)
        self.follow_stop_dist = float(self.get_parameter('follow_stop_dist').value)
        self.low_volt_threshold = float(self.get_parameter('low_volt_threshold').value)
        self.low_volt_min_valid = float(self.get_parameter('low_volt_min_valid').value)
        self.low_volt_hold_sec = float(self.get_parameter('low_volt_hold_sec').value)

        self.waypoints: list[dict] = []
        self.all_waypoints: list[dict] = []
        self.dock_pose: dict | None = None
        self.low_volt_since: float | None = None
        self.low_volt_returning: bool = False
        self.custom_order_active = False
        self.single_goal_active = False
        self.single_goal_wp: dict | None = None
        self.enabled = False
        self.state = 'IDLE'
        self.wp_idx = 0
        self.retry_count = 0
        self.dwell_until = 0.0
        self.goal_handle = None
        self._internal_canceled_goals: set[int] = set()
        self._goal_seq = 0
        self._active_goal_seq = 0
        self._active_goal_kind = ''
        self._active_goal_wp: dict | None = None
        self.waiting_goal_response = False
        self.current_pose: tuple[float, float] | None = None

        # 사람 추종(3초 연속 감지 확정 -> 음성 동시 30초 비주얼 서보잉, bh >= 250px ≈ 1.2m 기준) 상태
        self.zone_enable = True
        self.follow_trigger_bh = 250.0
        self.follow_confirm_sec = 3.0
        self.person_detect_since: float | None = None
        self.person_detect_last = 0.0
        self.following_person = False
        self.follow_until = 0.0
        self.follow_cooldown_until = 0.0
        self.last_person_seen = 0.0
        self.last_person_err_x = 0.0
        self.last_person_bh = 0.0
        self.min_front_dist = 9.9

        amcl_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        scan_qos = QoSProfile(
            depth=5,
            reliability=ReliabilityPolicy.BEST_EFFORT,
        )
        self.create_subscription(
            PoseWithCovarianceStamped, '/amcl_pose', self._amcl_pose_cb, amcl_qos
        )
        self.create_subscription(LaserScan, '/scan', self._scan_cb, scan_qos)
        self.create_subscription(BatteryState, '/battery_state', self._battery_cb, scan_qos)
        self.create_subscription(Bool, '/yolo/zone_enable', self._zone_enable_cb, 10)
        self.create_subscription(String, '/yolo/detections', self._detections_cb, 10)
        self.create_subscription(String, '/gesture/cmd', self._gesture_cmd_cb, 10)

        self.nav_client = ActionClient(self, NavigateToPose, '/navigate_to_pose')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.status_pub = self.create_publisher(String, '/patrol/status', 10)
        self.tts_pub = self.create_publisher(String, '/tts/say', 10)
        self.sound_pub = self.create_publisher(String, '/play/sound', 10)
        self.create_service(SetBool, '/patrol/set_enabled', self._set_enabled_cb)

        self.timer = self.create_timer(0.5, self._tick)
        self.follow_timer = self.create_timer(0.1, self._follow_tick)
        self._load_waypoints()

        if bool(self.get_parameter('auto_start').value) and self.waypoints:
            self.enabled = True
            self.get_logger().info('Patrol auto_start=True -> starting patrol.')
        else:
            self.get_logger().info(
                'Patrol node ready (IDLE). Call /patrol/set_enabled {data: true} to start.'
            )

    def _load_waypoints(self) -> bool:
        """YAML 파일에서 웨이포인트와 순찰 파라미터를 로드."""
        if not os.path.isfile(self.waypoints_file):
            self.get_logger().error(f'Waypoints file not found: {self.waypoints_file}')
            self.waypoints = []
            return False

        try:
            with open(self.waypoints_file, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            patrol_cfg = data.get('patrol', {})
            if 'dwell_sec' in patrol_cfg:
                self.dwell_sec = float(patrol_cfg['dwell_sec'])
            if 'retry_limit' in patrol_cfg:
                self.retry_limit = int(patrol_cfg['retry_limit'])

            raw_dock = patrol_cfg.get('dock')
            if isinstance(raw_dock, dict) and 'x' in raw_dock and 'y' in raw_dock:
                self.dock_pose = {
                    'name': 'DOCK',
                    'x': float(raw_dock['x']),
                    'y': float(raw_dock['y']),
                    'yaw': float(raw_dock.get('yaw', 0.0)),
                }
            else:
                self.dock_pose = None

            raw_wps = patrol_cfg.get('waypoints', [])
            self.all_waypoints = [
                {
                    'name': str(wp.get('name', f'WP{i + 1}')),
                    'x': float(wp['x']),
                    'y': float(wp['y']),
                    'yaw': float(wp.get('yaw', 0.0)),
                }
                for i, wp in enumerate(raw_wps)
            ]
            custom_order = patrol_cfg.get('custom_order', [])
            parsed = []
            if isinstance(custom_order, list) and len(custom_order) > 0:
                self.custom_order_active = True
                for raw_idx in custom_order:
                    try:
                        idx = int(raw_idx)
                    except (TypeError, ValueError):
                        continue
                    if 0 <= idx < len(raw_wps):
                        wp = raw_wps[idx]
                        parsed.append({
                            'name': str(wp.get('name', f'WP{idx + 1}')),
                            'x': float(wp['x']),
                            'y': float(wp['y']),
                            'yaw': float(wp.get('yaw', 0.0)),
                        })
            else:
                self.custom_order_active = False
                for i, wp in enumerate(raw_wps):
                    if not bool(wp.get('enabled', True)):
                        continue
                    parsed.append({
                        'name': str(wp.get('name', f'WP{i + 1}')),
                        'x': float(wp['x']),
                        'y': float(wp['y']),
                        'yaw': float(wp.get('yaw', 0.0)),
                    })
            self.waypoints = parsed
            mode_tag = 'CUSTOM_ORDER' if self.custom_order_active else 'ALL_NEAREST'
            self.get_logger().info(
                f'Loaded {len(self.waypoints)} waypoints [{mode_tag}] '
                f'(dwell={self.dwell_sec}s, retry={self.retry_limit}).'
            )
            return len(self.waypoints) > 0
        except Exception as exc:
            self.get_logger().error(f'Failed to parse waypoints YAML: {exc}')
            self.waypoints = []
            return False

    def _amcl_pose_cb(self, msg: PoseWithCovarianceStamped):
        p = msg.pose.pose.position
        self.current_pose = (float(p.x), float(p.y))

    def _scan_cb(self, msg: LaserScan):
        min_d = 9.9
        angle = msg.angle_min
        inc = msg.angle_increment
        for r in msg.ranges:
            # LDS-01(0~2π) 및 (-π~π) 양쪽 포맷 모두 정면 ±25°(±0.436 rad) 대칭 감지
            norm_angle = math.atan2(math.sin(angle), math.cos(angle))
            if -0.436 <= norm_angle <= 0.436:
                if math.isfinite(r) and msg.range_min < r < msg.range_max:
                    if r < min_d:
                        min_d = r
            angle += inc
        self.min_front_dist = min_d

    def _zone_enable_cb(self, msg: Bool):
        self.zone_enable = bool(msg.data)

    def _battery_cb(self, msg: BatteryState):
        """OpenCR 저전압(9.0V < V <= 10.5V) 5초 지속 시 지정 충전소(dock) 자동 복귀."""
        voltage = float(msg.voltage)
        if not math.isfinite(voltage):
            return

        # 충분한 전압 회복 시 래치 해제 (배터리 교체/충전 후)
        if voltage >= 11.5:
            self.low_volt_since = None
            self.low_volt_returning = False
            return

        if self.low_volt_returning:
            return

        if self.low_volt_min_valid < voltage <= self.low_volt_threshold:
            now = time.monotonic()
            if self.low_volt_since is None:
                self.low_volt_since = now
                return
            if (now - self.low_volt_since) < self.low_volt_hold_sec:
                return

            self.low_volt_since = None
            self._load_waypoints()

            if self.dock_pose is None:
                self.low_volt_returning = True
                self.get_logger().warn(
                    f'🔋 [Low Battery {voltage:.2f}V] Dock pose not configured -> stopping patrol.'
                )
                self.enabled = False
                self.single_goal_active = False
                self.single_goal_wp = None
                self.following_person = False
                self.person_detect_since = None
                self._cancel_active_goal()
                self.stop_robot()
                self._publish_tts('배터리가 부족하지만 충전소 위치가 지정되지 않았습니다.')
                self._publish_status('LOW_BATTERY:NO_DOCK')
                return

            if not self.nav_client.server_is_ready():
                self.nav_client.wait_for_server(timeout_sec=0.5)
                if not self.nav_client.server_is_ready():
                    # Nav2 미준비 시 영구 래치하지 않고 5초 뒤 재확인할 수 있게 보류
                    self.low_volt_since = now
                    self.enabled = False
                    self.following_person = False
                    self.person_detect_since = None
                    self._cancel_active_goal()
                    self.stop_robot()
                    self._publish_tts('배터리가 부족합니다. 주행을 정지합니다.')
                    self._publish_status('LOW_BATTERY:NO_NAV2')
                    return

            self.low_volt_returning = True
            self.get_logger().warn(
                f'🔋 [Low Battery {voltage:.2f}V >= {self.low_volt_hold_sec:.0f}s] Returning to DOCK '
                f"({self.dock_pose['x']:.2f}, {self.dock_pose['y']:.2f})."
            )
            self.enabled = False
            self.following_person = False
            self.person_detect_since = None
            self._cancel_active_goal(internal=True)
            dock_target = dict(self.dock_pose)
            self._publish_tts('배터리가 부족하여 충전 위치로 복귀합니다.')
            self._send_waypoint_goal(dock_target, status_prefix='DOCK_RETURN', kind='single')
        else:
            self.low_volt_since = None

    def _gesture_cmd_cb(self, msg: String):
        """Jetson MediaPipe 손 제스처 명령(/gesture/cmd) 처리: 비상정지(ESTOP) 및 WP 1~10 단일 이동."""
        try:
            data = json.loads(msg.data)
        except Exception:
            return
        cmd = str(data.get('cmd', '')).upper()

        if cmd == 'ESTOP':
            self.enabled = False
            self.single_goal_active = False
            self.single_goal_wp = None
            self.following_person = False
            self.person_detect_since = None
            self.follow_cooldown_until = max(self.follow_cooldown_until, time.monotonic() + 5.0)
            self._cancel_active_goal()
            self.stop_robot()
            self._publish_tts('비상 정지합니다.')
            self._publish_status('ESTOP:GESTURE')
            self.get_logger().warn('🛑 [Gesture ESTOP] Canceled active goal & stopped robot.')
            return

        if cmd == 'GOTO_WP':
            try:
                wp_index = int(data.get('wp_index', 0))
            except (TypeError, ValueError):
                return
            self._load_waypoints()
            if wp_index < 1 or wp_index > len(self.all_waypoints):
                self.get_logger().warn(
                    f'Gesture GOTO_WP {wp_index} out of range (registered: {len(self.all_waypoints)}).'
                )
                self._publish_tts(f'{wp_index}번 웨이포인트가 등록되어 있지 않습니다.')
                return

            if not self.nav_client.server_is_ready():
                self.nav_client.wait_for_server(timeout_sec=0.5)
                if not self.nav_client.server_is_ready():
                    self._publish_tts('내비게이션 서버가 준비되지 않았습니다.')
                    self._publish_status('WAITING_NAV2')
                    return

            target_wp = dict(self.all_waypoints[wp_index - 1])
            self.enabled = False
            self.following_person = False
            self.person_detect_since = None
            self.follow_cooldown_until = max(self.follow_cooldown_until, time.monotonic() + 5.0)
            self._cancel_active_goal(internal=True)
            self._publish_tts(f'{wp_index}번 웨이포인트로 이동합니다.')
            self._send_waypoint_goal(target_wp, status_prefix='GESTURE_NAV', kind='single')
            return

    def _detections_cb(self, msg: String):
        if not self.follow_person_enabled:
            return
        # 저전압 충전 복귀 또는 단일 WP 이동 중에는 사람 추종으로 탈선 금지
        if self.low_volt_returning or self.single_goal_active:
            self.person_detect_since = None
            return
        if not self.following_person and not self.zone_enable:
            return
        if not self.enabled and not self.nav_client.server_is_ready():
            return
        try:
            data = json.loads(msg.data)
        except Exception:
            return
        if data.get('zone_paused'):
            return
        img_size = data.get('image_size') or [640, 480]
        img_w = float(img_size[0]) if len(img_size) >= 1 and img_size[0] else 640.0

        best_person = None
        best_bh = 0.0
        for obj in data.get('objects', []):
            if obj.get('class_name') == 'person' and float(obj.get('conf', 0.0)) >= 0.50:
                size = obj.get('size') or [0.0, 0.0]
                bh = float(size[1]) if len(size) >= 2 else 0.0
                if bh >= best_bh:
                    best_bh = bh
                    best_person = obj

        now = time.monotonic()
        if best_person is None:
            if self.person_detect_since is not None and (now - self.person_detect_last) > 0.8:
                self.person_detect_since = None
            return

        center = best_person.get('center') or [img_w / 2.0, 0.0]
        cx = float(center[0])
        self.last_person_seen = now
        self.last_person_err_x = (img_w / 2.0 - cx) / (img_w / 2.0)
        self.last_person_bh = best_bh

        if not self.following_person:
            if best_bh >= self.follow_trigger_bh and now >= self.follow_cooldown_until:
                if self.person_detect_since is None or (now - self.person_detect_last) > 0.8:
                    self.person_detect_since = now
                self.person_detect_last = now
                if (now - self.person_detect_since) >= self.follow_confirm_sec:
                    self.person_detect_since = None
                    self.following_person = True
                    self.follow_until = now + self.follow_duration_sec
                    self.follow_cooldown_until = now + self.follow_cooldown_sec
                    self._cancel_active_goal(internal=True)
                    self._publish_status('FOLLOWING:PERSON')
                    self.get_logger().info(
                        f'Person confirmed >= {self.follow_confirm_sec:.0f}s (bh={best_bh:.0f}px) -> following for {self.follow_duration_sec:.0f}s (cooldown {self.follow_cooldown_sec:.0f}s).'
                    )
            elif self.person_detect_since is not None and (now - self.person_detect_last) > 0.8:
                self.person_detect_since = None

    def _find_nearest_wp_idx(self) -> int:
        if not self.waypoints or self.current_pose is None:
            return 0
        px, py = self.current_pose
        best_idx = 0
        best_dist = float('inf')
        for i, wp in enumerate(self.waypoints):
            d = math.hypot(wp['x'] - px, wp['y'] - py)
            if d < best_dist:
                best_dist = d
                best_idx = i
        return best_idx

    def _publish_status(self, text: str):
        self.state = text
        msg = String()
        msg.data = text
        self.status_pub.publish(msg)

    def _publish_tts(self, text: str):
        msg = String()
        msg.data = text
        self.tts_pub.publish(msg)

    def _stop_sound(self):
        msg = String()
        msg.data = 'stop'
        self.sound_pub.publish(msg)

    def stop_robot(self):
        """종료 및 순찰 중단 시 /cmd_vel 0.0을 5회 연속 발행."""
        stop_msg = Twist()
        for _ in range(5):
            if rclpy.ok():
                self.cmd_pub.publish(stop_msg)
            time.sleep(0.05)

    def _cancel_active_goal(self, internal: bool = False):
        # 진행 중이던 send_goal_async 응답이 뒤늦게 도착해도 즉시 취소되도록 시퀀스 무효화
        self._goal_seq += 1
        self._active_goal_seq = 0
        self._active_goal_kind = ''
        self._active_goal_wp = None
        if self.goal_handle is not None:
            if internal:
                self._internal_canceled_goals.add(id(self.goal_handle))
            try:
                self.goal_handle.cancel_goal_async()
            except Exception as exc:
                self.get_logger().warn(f'Goal cancel warning: {exc}')
            self.goal_handle = None
        self.waiting_goal_response = False

    def _set_enabled_cb(self, request: SetBool.Request, response: SetBool.Response):
        if request.data:
            if not self._load_waypoints():
                response.success = False
                response.message = 'Failed to load valid waypoints.'
                return response
            self.wp_idx = 0 if self.custom_order_active else self._find_nearest_wp_idx()
            self.retry_count = 0
            self.dwell_until = 0.0
            self.single_goal_active = False
            self.single_goal_wp = None
            self._cancel_active_goal(internal=True)
            self.enabled = True
            self._publish_status('STARTING')
            start_wp = self.waypoints[self.wp_idx]['name']
            route_desc = ' -> '.join(w['name'] for w in self.waypoints)
            mode_label = 'Custom patrol' if self.custom_order_active else 'Patrol'
            response.success = True
            response.message = f'{mode_label} started from [{start_wp}] ({route_desc}).'
            self.get_logger().info(response.message)
        else:
            was_following = self.following_person
            self.enabled = False
            self.single_goal_active = False
            self.single_goal_wp = None
            self.following_person = False
            self.person_detect_since = None
            self.follow_cooldown_until = max(self.follow_cooldown_until, time.monotonic() + 5.0)
            self._cancel_active_goal()
            self.stop_robot()
            if was_following:
                self._stop_sound()
                self._publish_tts('추적을 종료합니다.')
            self._publish_status('STOPPED')
            response.success = True
            response.message = 'Patrol stopped.'
            self.get_logger().info('Patrol stopped by service request.')
        return response

    def _send_waypoint_goal(self, wp: dict, status_prefix: str = 'NAVIGATING', kind: str = 'patrol'):
        qz, qw = yaw_to_quaternion(wp['yaw'])

        goal = NavigateToPose.Goal()
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = wp['x']
        pose.pose.position.y = wp['y']
        pose.pose.orientation.z = qz
        pose.pose.orientation.w = qw
        goal.pose = pose

        wp_copy = dict(wp)
        self._goal_seq += 1
        seq = self._goal_seq
        self._active_goal_seq = seq
        self._active_goal_kind = kind
        self._active_goal_wp = wp_copy
        self.single_goal_active = (kind == 'single')
        self.single_goal_wp = wp_copy if kind == 'single' else None

        self.waiting_goal_response = True
        self._publish_status(f"{status_prefix}:{wp_copy['name']}")
        self.get_logger().info(
            f"Sending goal [{wp_copy['name']}] ({wp_copy['x']:.2f}, {wp_copy['y']:.2f}, yaw={wp_copy['yaw']:.2f})"
        )
        send_future = self.nav_client.send_goal_async(goal)
        send_future.add_done_callback(
            lambda f, s=seq, k=kind, w=wp_copy: self._goal_response_cb(f, s, k, w)
        )

    def _send_current_waypoint(self):
        if not self.waypoints or self.waiting_goal_response or self.goal_handle is not None:
            return
        if not self.nav_client.server_is_ready():
            self.nav_client.wait_for_server(timeout_sec=0.2)
            if not self.nav_client.server_is_ready():
                self._publish_status('WAITING_NAV2')
                return

        self._send_waypoint_goal(self.waypoints[self.wp_idx], status_prefix='NAVIGATING', kind='patrol')

    def _goal_response_cb(self, future, seq: int = 0, kind: str = 'patrol', wp: dict | None = None):
        is_current = (seq == self._active_goal_seq)
        if is_current:
            self.waiting_goal_response = False

        try:
            handle = future.result()
        except Exception as exc:
            self.get_logger().error(f'Goal send failed: {exc}')
            if is_current:
                self._active_goal_seq = 0
                self._active_goal_kind = ''
                self._active_goal_wp = None
                if kind == 'single':
                    self.single_goal_active = False
                    self.single_goal_wp = None
                    self._publish_status('IDLE')
                else:
                    self._handle_waypoint_failure()
            return

        stale = (
            not is_current
            or self.following_person
            or (kind == 'patrol' and not self.enabled)
            or (kind == 'single' and not self.single_goal_active)
        )

        if not handle.accepted:
            self.get_logger().warn('Goal rejected by Nav2.')
            if stale:
                return
            self._active_goal_seq = 0
            self._active_goal_kind = ''
            self._active_goal_wp = None
            if kind == 'single':
                wp_name = wp['name'] if wp else 'WP'
                self.single_goal_active = False
                self.single_goal_wp = None
                self._publish_tts(f'{wp_name} 이동이 거부되었습니다.')
                self._publish_status('IDLE')
            else:
                self._handle_waypoint_failure()
            return

        # 응답 대기 중 ESTOP·정지·추종·신규 Goal로 전환된 경우 즉시 내부 취소 (Nav2 /cmd_vel 충돌 차단)
        if stale:
            self._internal_canceled_goals.add(id(handle))
            try:
                handle.cancel_goal_async()
            except Exception as exc:
                self.get_logger().warn(f'Stale goal cancel warning: {exc}')
            self.get_logger().info(
                f'Canceled stale Nav2 goal (seq={seq}, kind={kind}) accepted after state change.'
            )
            return

        self.goal_handle = handle
        result_future = handle.get_result_async()
        result_future.add_done_callback(
            lambda f, h=handle, s=seq, k=kind, w=wp: self._goal_result_cb(f, h, s, k, w)
        )

    def _goal_result_cb(self, future, handle=None, seq: int = 0, kind: str = 'patrol', wp: dict | None = None):
        if handle is None or self.goal_handle is handle:
            self.goal_handle = None
        is_internal_cancel = handle is not None and id(handle) in self._internal_canceled_goals
        if handle is not None:
            self._internal_canceled_goals.discard(id(handle))

        # 내부 취소되었거나 이미 새 Goal로 교체된 구버전 콜백은 새 상태를 건드리지 않고 즉시 종료
        if is_internal_cancel or (seq != 0 and seq != self._active_goal_seq):
            if is_internal_cancel:
                wp_name = wp['name'] if wp else 'WP'
                self.get_logger().info(
                    f'Goal [{wp_name}] canceled internally (seq={seq}, kind={kind}) -> ignoring stale result.'
                )
            return

        self._active_goal_seq = 0
        self._active_goal_kind = ''
        self._active_goal_wp = None

        if kind == 'single':
            wp_name = wp['name'] if wp else (self.single_goal_wp['name'] if self.single_goal_wp else 'WP')
            self.single_goal_active = False
            self.single_goal_wp = None
            try:
                status = future.result().status
            except Exception:
                status = GoalStatus.STATUS_ABORTED
            if status == GoalStatus.STATUS_SUCCEEDED:
                if wp_name == 'DOCK':
                    self.stop_robot()
                    self.get_logger().info('🔋 Low-battery DOCK arrival complete.')
                    self._publish_tts('충전 위치에 도착했습니다.')
                    self._publish_status('DOCKED')
                else:
                    self.get_logger().info(f'Gesture single goal [{wp_name}] reached.')
                    self._publish_tts(f'{wp_name}에 도착했습니다.')
                    self._publish_status('IDLE')
            elif status == GoalStatus.STATUS_CANCELED:
                self._publish_status('IDLE')
            else:
                self.get_logger().warn(f'Single goal [{wp_name}] failed (status={status}).')
                if wp_name == 'DOCK':
                    self._publish_tts('충전 위치 이동에 실패했습니다.')
                    self._publish_status('LOW_BATTERY:FAILED')
                else:
                    self._publish_tts(f'{wp_name} 이동에 실패했습니다.')
                    self._publish_status('IDLE')
            return

        if not self.enabled:
            return

        try:
            result = future.result()
            status = result.status
        except Exception as exc:
            self.get_logger().error(f'Goal result error: {exc}')
            self._handle_waypoint_failure()
            return

        wp_cur = wp if wp is not None else self.waypoints[self.wp_idx]
        if status == GoalStatus.STATUS_SUCCEEDED:
            self.get_logger().info(f"Reached [{wp_cur['name']}]. Dwelling {self.dwell_sec:.1f}s.")
            self.retry_count = 0
            self.dwell_until = time.monotonic() + self.dwell_sec
            self._publish_status(f"DWELLING:{wp_cur['name']}")
        elif status == GoalStatus.STATUS_CANCELED:
            self.get_logger().warn(f"Goal [{wp_cur['name']}] canceled externally. Stopping patrol.")
            self.enabled = False
            self._publish_status('IDLE')
        else:
            self.get_logger().warn(f"Goal [{wp_cur['name']}] failed (status={status}).")
            self._handle_waypoint_failure()

    def _handle_waypoint_failure(self):
        if not self.enabled or not self.waypoints:
            return
        wp = self.waypoints[self.wp_idx]
        if self.retry_count < self.retry_limit:
            self.retry_count += 1
            self.get_logger().warn(
                f"Retrying [{wp['name']}] ({self.retry_count}/{self.retry_limit})..."
            )
            self.dwell_until = time.monotonic() + 1.0
            self._publish_status(f"RETRY:{wp['name']}")
        else:
            self.get_logger().warn(
                f"Skipping [{wp['name']}] after {self.retry_limit} retries -> advancing to next."
            )
            self.retry_count = 0
            self.wp_idx = (self.wp_idx + 1) % len(self.waypoints)
            self.dwell_until = time.monotonic() + 0.5
            self._publish_status(f"SKIPPED:{wp['name']}")

    def _follow_tick(self):
        if not self.following_person:
            return
        now = time.monotonic()
        if (
            now >= self.follow_until
            or (now - self.last_person_seen) > 2.5
        ):
            self.following_person = False
            self.stop_robot()
            self._stop_sound()
            self._publish_tts('추적을 종료합니다.')
            if self.enabled:
                self.dwell_until = now + 0.5
                self._publish_status('RESUMING:PATROL')
                self.get_logger().info('Person follow ended -> resuming patrol.')
            else:
                self._publish_status('IDLE')
                self.get_logger().info('Person follow ended -> returning to IDLE.')
            return

        twist = Twist()
        err_x = self.last_person_err_x
        if abs(err_x) > 0.08:
            twist.angular.z = max(-0.7, min(0.7, err_x * 0.9))

        too_close = self.min_front_dist <= self.follow_stop_dist or self.last_person_bh >= 320.0
        if not too_close and (now - self.last_person_seen) <= 0.6:
            twist.linear.x = 0.05 if abs(err_x) > 0.5 else 0.14

        self.cmd_pub.publish(twist)

    def _tick(self):
        if not self.enabled or not self.waypoints or self.following_person:
            return
        if self.waiting_goal_response or self.goal_handle is not None:
            return
        now = time.monotonic()
        if self.dwell_until > 0.0:
            if now < self.dwell_until:
                return
            self.dwell_until = 0.0
            if self.state.startswith('DWELLING:'):
                self.wp_idx = (self.wp_idx + 1) % len(self.waypoints)
        self._send_current_waypoint()


def main(args=None):
    stop_requested = False

    def _on_signal(signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    if SignalHandlerOptions is not None:
        rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)
    else:
        rclpy.init(args=args)

    node = PatrolNode()
    try:
        if SignalHandlerOptions is not None:
            while rclpy.ok() and not stop_requested:
                rclpy.spin_once(node, timeout_sec=0.1)
        else:
            rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node._cancel_active_goal()
        node.stop_robot()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
[Tier 2 Jetson] 전 구간 상시 바닥 장애물 감지 노드 (floor_obstacle_node.py)
- 기존 YOLO(/yolo/zone_enable)와 완전 분리되어 /camera/image_raw/compressed를 상시 구독
- 2D LiDAR 사각지대(저고도 바닥 물체)를 OpenCV(LAB 색차 + 에지/형태학 필터)로 경량 검출
- 선택구역 내부에서 /yolo/detections가 수신되면 하단 좌표를 보너스 합산
- 검출된 바닥 물체를 로봇 기준 전방 LaserScan(/vision/scan)으로 변환해 Nav2 local_costmap에 전달
"""
import json
import math
import time
import numpy as np
import cv2

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import CompressedImage, LaserScan
from std_msgs.msg import String


class FloorObstacleDetector:
    """순수 OpenCV 기반 바닥 장애물 감지기 (ROS 비의존, 오프라인 테스트 가능)."""

    def __init__(
        self,
        cam_height: float = 0.145,
        cam_pitch_deg: float = 0.0,
        cam_x_offset: float = 0.06,
        hfov_deg: float = 62.2,
        vfov_deg: float = 48.8,
        roi_top_ratio: float = 0.58,
        roi_bottom_ratio: float = 0.99,
        chroma_thresh: float = 18.0,
        luma_thresh: float = 38.0,
        edge_low: int = 40,
        edge_high: int = 110,
        min_area_px: int = 650,
        min_box_w: int = 14,
        min_box_h: int = 12,
        max_detect_dist: float = 1.50,
        min_detect_dist: float = 0.25,
    ):
        self.cam_height = float(cam_height)
        self.cam_pitch_rad = math.radians(cam_pitch_deg)
        self.cam_x_offset = float(cam_x_offset)
        self.hfov_rad = math.radians(hfov_deg)
        self.vfov_rad = math.radians(vfov_deg)
        self.roi_top_ratio = float(roi_top_ratio)
        self.roi_bottom_ratio = float(roi_bottom_ratio)
        self.chroma_thresh = float(chroma_thresh)
        self.luma_thresh = float(luma_thresh)
        self.edge_low = int(edge_low)
        self.edge_high = int(edge_high)
        self.min_area_px = int(min_area_px)
        self.min_box_w = int(min_box_w)
        self.min_box_h = int(min_box_h)
        self.max_detect_dist = float(max_detect_dist)
        self.min_detect_dist = float(min_detect_dist)

        # 바닥 기준색 EMA (초기 프레임에서 자동 학습, 완만한 조명 변화 추적)
        self.floor_lab_ema = None
        self.kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self.kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
        self.kernel_dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))

    def pixel_to_robot_xy(self, u: float, v: float, img_w: int, img_h: int):
        """지면(z=0) 위의 픽셀 (u, v)을 로봇 base_footprint 기준 (x_forward, y_left) 미터로 투영."""
        cx = img_w * 0.5
        cy = img_h * 0.5
        fx = (img_w * 0.5) / math.tan(self.hfov_rad * 0.5)
        fy = (img_h * 0.5) / math.tan(self.vfov_rad * 0.5)

        # 카메라 광축 기준 하방 각도
        ray_pitch = math.atan2(v - cy, fy) + self.cam_pitch_rad
        if ray_pitch <= 0.03:
            return None

        z_cam = self.cam_height / math.tan(ray_pitch)
        if z_cam < self.min_detect_dist or z_cam > self.max_detect_dist:
            return None

        x_robot = z_cam + self.cam_x_offset
        # 이미지 오른쪽(+u)은 로봇 우측(-y)
        y_robot = -z_cam * ((u - cx) / fx)
        return (x_robot, y_robot)

    def detect(self, bgr: np.ndarray, yolo_objects=None, draw_debug: bool = False):
        """
        BGR 프레임에서 바닥 장애물 영역을 검출하고 (x, y) 포인트 리스트 및 디버그 이미지를 반환.
        반환: (points_xy: list[(x, y)], boxes: list[(x1, y1, x2, y2, src)], debug_img or None)
        """
        img_h, img_w = bgr.shape[:2]
        roi_y1 = max(0, min(img_h - 2, int(img_h * self.roi_top_ratio)))
        roi_y2 = max(roi_y1 + 2, min(img_h, int(img_h * self.roi_bottom_ratio)))

        roi = bgr[roi_y1:roi_y2, :]
        blurred = cv2.GaussianBlur(roi, (5, 5), 0)
        lab = cv2.cvtColor(blurred, cv2.COLOR_BGR2LAB).astype(np.float32)

        # 1. 바닥 기준 LAB 추정 (중앙 하단 영역의 중앙값 기반 EMA로 조명 변화 적응)
        rh, rw = lab.shape[:2]
        ref_patch = lab[int(rh * 0.25):, int(rw * 0.15):int(rw * 0.85)]
        med_lab = np.median(ref_patch.reshape(-1, 3), axis=0)
        if self.floor_lab_ema is None:
            self.floor_lab_ema = med_lab.copy()
        else:
            # 급격한 변화(로봇 코앞을 큰 물체가 가린 경우)에는 기준색 오염 방지
            diff_med = float(np.linalg.norm(med_lab[1:] - self.floor_lab_ema[1:]))
            if diff_med < self.chroma_thresh:
                self.floor_lab_ema = 0.92 * self.floor_lab_ema + 0.08 * med_lab

        # 2. 색차(Chroma) + 명도 편차(Luma) 계산
        dL_signed = self.floor_lab_ema[0] - lab[:, :, 0]
        dL = np.abs(dL_signed)
        da = lab[:, :, 1] - self.floor_lab_ema[1]
        db = lab[:, :, 2] - self.floor_lab_ema[2]
        dC = np.sqrt(da * da + db * db)

        # 3. 구조적 에지 마스크 (부드러운 반사광·그림자·1px 타일 줄눈 배제)
        gray = cv2.cvtColor(blurred, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, self.edge_low, self.edge_high)
        edges_dilated = cv2.dilate(edges, self.kernel_dilate, iterations=1)

        # 색상이 뚜렷하게 다른 물체 OR 명도가 크게 다르면서 경계가 뚜렷한 물체 OR 아주 어두운 물체(밝은 반사광 배제)
        mask_chroma = (dC > self.chroma_thresh).astype(np.uint8) * 255
        mask_luma = ((dL > self.luma_thresh) & (edges_dilated > 0)).astype(np.uint8) * 255
        mask_strong_dark = (dL_signed > (self.luma_thresh * 1.7)).astype(np.uint8) * 255

        combined = cv2.bitwise_or(mask_chroma, cv2.bitwise_or(mask_luma, mask_strong_dark))
        # 형태학 열기(1~2px 타일 줄눈·점 잡음 제거) ➔ 닫기(물체 내부 구멍 메우기)
        cleaned = cv2.morphologyEx(combined, cv2.MORPH_OPEN, self.kernel_open)
        cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, self.kernel_close)

        contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        boxes = []
        points_xy = []

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < self.min_area_px:
                continue
            bx, by, bw, bh = cv2.boundingRect(cnt)
            if bw < self.min_box_w or bh < self.min_box_h:
                continue
            # ROI 전체 가로를 덮는 수평 그림자/경계 띠 배제
            if bw > int(img_w * 0.85) and bh < int(rh * 0.25):
                continue

            x1 = bx
            y1 = by + roi_y1
            x2 = bx + bw
            y2 = by + bh + roi_y1
            boxes.append((x1, y1, x2, y2, "cv"))

            # 물체 하단 변(바닥 접지선)을 따라 여러 픽셀 샘플링해 LaserScan 폭 반영
            step = max(8, bw // 8)
            for u in range(x1, x2 + 1, step):
                pt = self.pixel_to_robot_xy(float(u), float(y2), img_w, img_h)
                if pt is not None:
                    points_xy.append(pt)

        # 4. 선택구역 내부 보너스: YOLO 검출 객체의 바닥 접지선 합산
        if yolo_objects:
            for obj in yolo_objects:
                box = obj.get("box")
                if not box or len(box) != 4:
                    continue
                x1, y1, x2, y2 = [int(v) for v in box]
                if y2 < roi_y1:
                    continue
                y2_clamped = min(img_h - 1, max(roi_y1, y2))
                boxes.append((x1, max(0, y1), x2, y2_clamped, f"yolo:{obj.get('class_name', '')}"))
                bw = max(1, x2 - x1)
                step = max(8, bw // 8)
                for u in range(max(0, x1), min(img_w - 1, x2) + 1, step):
                    pt = self.pixel_to_robot_xy(float(u), float(y2_clamped), img_w, img_h)
                    if pt is not None:
                        points_xy.append(pt)

        debug_img = None
        if draw_debug:
            debug_img = bgr.copy()
            cv2.line(debug_img, (0, roi_y1), (img_w, roi_y1), (0, 220, 0), 1)
            for x1, y1, x2, y2, src in boxes:
                color = (0, 0, 255) if src == "cv" else (0, 165, 255)
                cv2.rectangle(debug_img, (x1, y1), (x2, y2), color, 2)
                pt_mid = self.pixel_to_robot_xy((x1 + x2) * 0.5, float(y2), img_w, img_h)
                label = f"{src} {pt_mid[0]:.2f}m" if pt_mid else src
                cv2.putText(
                    debug_img, label, (x1, max(15, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA
                )

        return points_xy, boxes, debug_img


class FloorObstacleNode(Node):
    """Jetson 독립 상시 바닥 장애물 감지 ROS 2 노드 (/vision/scan 발행)."""

    def __init__(self):
        super().__init__('floor_obstacle_node')

        # 캘리브레이션 및 감도 파라미터 (현장 튜닝 가능)
        self.declare_parameter('cam_height', 0.145)
        self.declare_parameter('cam_pitch_deg', 0.0)
        self.declare_parameter('cam_x_offset', 0.06)
        self.declare_parameter('hfov_deg', 62.2)
        self.declare_parameter('vfov_deg', 48.8)
        self.declare_parameter('roi_top_ratio', 0.58)
        self.declare_parameter('chroma_thresh', 18.0)
        self.declare_parameter('luma_thresh', 38.0)
        self.declare_parameter('min_area_px', 650)
        self.declare_parameter('max_detect_dist', 1.50)
        self.declare_parameter('confirm_frames', 4)
        self.declare_parameter('beam_hold_sec', 0.0)
        self.declare_parameter('lateral_padding_m', 0.03)
        self.declare_parameter('frame_id', 'base_footprint')

        self.detector = FloorObstacleDetector(
            cam_height=self.get_parameter('cam_height').value,
            cam_pitch_deg=self.get_parameter('cam_pitch_deg').value,
            cam_x_offset=self.get_parameter('cam_x_offset').value,
            hfov_deg=self.get_parameter('hfov_deg').value,
            vfov_deg=self.get_parameter('vfov_deg').value,
            roi_top_ratio=self.get_parameter('roi_top_ratio').value,
            chroma_thresh=self.get_parameter('chroma_thresh').value,
            luma_thresh=self.get_parameter('luma_thresh').value,
            min_area_px=self.get_parameter('min_area_px').value,
            max_detect_dist=self.get_parameter('max_detect_dist').value,
        )
        self.confirm_frames = max(1, int(self.get_parameter('confirm_frames').value))
        self.beam_hold_sec = max(0.0, float(self.get_parameter('beam_hold_sec').value))
        self.lateral_padding_m = max(0.0, float(self.get_parameter('lateral_padding_m').value))
        self.frame_id = str(self.get_parameter('frame_id').value)

        # LaserScan 스펙 (전방 ±33°, 1° 간격, 67빔)
        self.angle_min = -0.58
        self.angle_max = 0.58
        self.angle_increment = 0.0175
        self.num_beams = int(round((self.angle_max - self.angle_min) / self.angle_increment)) + 1
        self.range_min = 0.20
        self.range_max = 2.00
        # obstacle_max_range(1.50m)보다 크고 raytrace_max_range(1.80m)로 클리어되는 안전 값
        self.clear_range = 1.95

        # 연속 프레임 확정 버퍼 + 잔상 유지(Persistence Hold) 버퍼
        self.beam_hits = np.zeros(self.num_beams, dtype=np.int32)
        self.beam_hold_until = np.zeros(self.num_beams, dtype=np.float64)
        self.beam_hold_dist = np.full(self.num_beams, self.clear_range, dtype=np.float32)

        # YOLO 보너스 캐시 (선택구역 밖이면 자동 만료, 바닥 감지는 계속 상시 작동)
        self.yolo_objects = []
        self.yolo_ts = 0.0

        # QoS: Nav2 ObstacleLayer 호환 BEST_EFFORT
        scan_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )
        self.pub_scan = self.create_publisher(LaserScan, '/vision/scan', scan_qos)
        self.pub_floor_json = self.create_publisher(String, '/vision/floor_obstacles', 5)
        self.pub_debug = self.create_publisher(CompressedImage, '/vision/floor_debug/compressed', 1)

        # 영상 상시 구독 (depth=1, /yolo/zone_enable 무관)
        self.sub_cam = self.create_subscription(
            CompressedImage,
            '/camera/image_raw/compressed',
            self.image_callback,
            1,
        )
        self.sub_yolo = self.create_subscription(
            String,
            '/yolo/detections',
            self.yolo_callback,
            10,
        )

        self.processing = False
        self.frame_count = 0
        self.get_logger().info(
            ">>> [FloorObstacle] 전 구간 상시 바닥 장애물 감지 노드 기동 완료 "
            f"(frame_id={self.frame_id}, ROI_top={self.detector.roi_top_ratio:.2f}, "
            f"confirm={self.confirm_frames}f -> /vision/scan)"
        )

    def yolo_callback(self, msg: String):
        try:
            data = json.loads(msg.data)
            if data.get("zone_paused"):
                self.yolo_objects = []
                return
            self.yolo_objects = data.get("objects", [])
            self.yolo_ts = time.monotonic()
        except Exception:
            pass

    def _build_scan_msg(self, points_xy, stamp):
        raw_ranges = np.full(self.num_beams, self.clear_range, dtype=np.float32)

        for x, y in points_xy:
            dist = math.hypot(x, y)
            if dist < self.range_min or dist > self.detector.max_detect_dist:
                continue
            angle = math.atan2(y, x)
            idx = int(round((angle - self.angle_min) / self.angle_increment))
            if self.lateral_padding_m > 0.0:
                pad_angle = math.atan2(self.lateral_padding_m, max(dist, self.range_min))
                pad_beams = max(1, int(round(pad_angle / self.angle_increment)))
            else:
                pad_beams = 0
            for b_idx in range(idx - pad_beams, idx + pad_beams + 1):
                if 0 <= b_idx < self.num_beams and dist < raw_ranges[b_idx]:
                    raw_ranges[b_idx] = dist

        # 다중 프레임 확정 필터 (+ 옵션 잔상 유지: 기본 0.0s, odom vision_obstacle_layer에 유지 위임)
        now = time.monotonic()
        detected_mask = raw_ranges <= self.detector.max_detect_dist
        self.beam_hits = np.where(detected_mask, np.minimum(self.beam_hits + 1, 5), 0)
        newly_confirmed = self.beam_hits >= self.confirm_frames
        if self.beam_hold_sec > 0.0:
            if np.any(newly_confirmed):
                self.beam_hold_until = np.where(newly_confirmed, now + self.beam_hold_sec, self.beam_hold_until)
                self.beam_hold_dist = np.where(newly_confirmed, raw_ranges, self.beam_hold_dist)
            active_hold = now <= self.beam_hold_until
            confirmed_ranges = np.where(
                newly_confirmed,
                raw_ranges,
                np.where(active_hold, self.beam_hold_dist, self.clear_range),
            )
        else:
            active_hold = np.zeros(self.num_beams, dtype=bool)
            confirmed_ranges = np.where(newly_confirmed, raw_ranges, self.clear_range)

        scan = LaserScan()
        scan.header.stamp = stamp
        scan.header.frame_id = self.frame_id
        scan.angle_min = self.angle_min
        scan.angle_max = self.angle_min + (self.num_beams - 1) * self.angle_increment
        scan.angle_increment = self.angle_increment
        scan.time_increment = 0.0
        scan.scan_time = 0.066
        scan.range_min = self.range_min
        scan.range_max = self.range_max
        scan.ranges = confirmed_ranges.tolist()
        return scan, int(np.sum(newly_confirmed | active_hold))

    def image_callback(self, msg: CompressedImage):
        if self.processing:
            return
        self.processing = True
        try:
            np_arr = np.frombuffer(msg.data, dtype=np.uint8)
            bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if bgr is None:
                return

            active_yolo = self.yolo_objects if (time.monotonic() - self.yolo_ts) < 1.5 else None
            need_debug = self.pub_debug.get_subscription_count() > 0

            points_xy, boxes, debug_img = self.detector.detect(
                bgr, yolo_objects=active_yolo, draw_debug=need_debug
            )

            # 현재 노드 시각으로 스탬프 (Pi5-Jetson 클록 편차에 의한 TF extrapolation 방지)
            stamp = self.get_clock().now().to_msg()
            scan_msg, hit_beams = self._build_scan_msg(points_xy, stamp)
            self.pub_scan.publish(scan_msg)

            # WebUI <canvas> 오버레이용 경량 JSON 발행 (/vision/floor_obstacles)
            img_h, img_w = bgr.shape[:2]
            json_boxes = []
            min_dist = None
            for x1, y1, x2, y2, src in boxes:
                pt_mid = self.detector.pixel_to_robot_xy((x1 + x2) * 0.5, float(y2), img_w, img_h)
                dist_val = round(float(math.hypot(pt_mid[0], pt_mid[1])), 2) if pt_mid else None
                if dist_val is not None and (min_dist is None or dist_val < min_dist):
                    min_dist = dist_val
                json_boxes.append({
                    "box": [int(x1), int(y1), int(x2), int(y2)],
                    "dist": dist_val,
                    "src": src,
                })
            floor_msg = String()
            floor_msg.data = json.dumps({
                "image_size": [int(img_w), int(img_h)],
                "roi_top_ratio": round(self.detector.roi_top_ratio, 3),
                "confirmed_beams": int(hit_beams),
                "min_dist": min_dist,
                "boxes": json_boxes,
            })
            self.pub_floor_json.publish(floor_msg)

            if need_debug and debug_img is not None:
                ok, enc = cv2.imencode('.jpg', debug_img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                if ok:
                    dbg_msg = CompressedImage()
                    dbg_msg.header.stamp = stamp
                    dbg_msg.format = 'jpeg'
                    dbg_msg.data = enc.tobytes()
                    self.pub_debug.publish(dbg_msg)

            self.frame_count += 1
            if self.frame_count % 75 == 0:
                self.get_logger().info(
                    f"[FloorObstacle] frames={self.frame_count} boxes={len(boxes)} confirmed_beams={hit_beams}"
                )
        except Exception as e:
            self.get_logger().warn(f"[FloorObstacle] 처리 오류: {e}")
        finally:
            self.processing = False


def main(args=None):
    rclpy.init(args=args)
    node = FloorObstacleNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()

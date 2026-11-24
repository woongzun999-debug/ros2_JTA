#!/usr/bin/env python3
"""
[젯슨 나노/오린 전용] 실시간 ROS 2 YOLO11 초경량 객체 인식 노드 (yolo11n 패키지)
- 0.1ms JSON 바운딩 박스/좌표 경량 발행 (/yolo/detections)
- 영상 재인코딩(plot/imencode) On/Off 스위치 탑재로 추론 지연 및 대역폭 최소화
"""
import os
import json
import numpy as np
import cv2

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CompressedImage
from std_msgs.msg import Bool, String

from ultralytics import YOLO
import torch


class JetsonYoloNode(Node):
    def __init__(self):
        super().__init__('yolo_detector')

        # 1. ROS 2 파라미터 선언 (yolo11n.launch.py 실기 표준과 일치: yolo11s.pt / 640 / 0.60)
        self.declare_parameter('model', '/root/ros2_ws/yolo11s.pt')
        self.declare_parameter('imgsz', 640)
        self.declare_parameter('conf', 0.60)
        self.declare_parameter('use_compressed', True)
        self.declare_parameter('publish_annotated', False)

        model_path = self.get_parameter('model').get_parameter_value().string_value
        self.imgsz = self.get_parameter('imgsz').get_parameter_value().integer_value
        self.conf = self.get_parameter('conf').get_parameter_value().double_value
        use_compressed = self.get_parameter('use_compressed').get_parameter_value().bool_value
        self.publish_annotated = self.get_parameter('publish_annotated').get_parameter_value().bool_value

        # 가중치 파일 탐색 (절대경로 or 워크스페이스 fallback)
        if not os.path.exists(model_path):
            alt_path = os.path.join('/root/ros2_ws', os.path.basename(model_path))
            if os.path.exists(alt_path):
                model_path = alt_path

        # GPU(CUDA) 가속 확인 및 모델 로드
        device = '0' if torch.cuda.is_available() else 'cpu'
        self.get_logger().info(
            f">>> [YOLO11n] 모델 로딩 중: {model_path} (디바이스: {device}, 해상도: {self.imgsz}, "
            f"임계값: {self.conf}, 시각화발행: {self.publish_annotated})"
        )
        
        self.model = YOLO(model_path)
        self.device = device
        self.get_logger().info(f">>> [YOLO11n] {model_path} 로드 완료!")

        # 2. 토픽 퍼블리셔
        # (1) 초경량 JSON 객체 검출 데이터 (/yolo/detections, String)
        self.pub_detections = self.create_publisher(String, '/yolo/detections', 10)

        # (2) 시각화 영상 토픽 (publish_annotated=True 일 때만 실제 인코딩/발행)
        if self.publish_annotated:
            self.pub_annotated = self.create_publisher(Image, '/yolo/image_annotated', 1)
            self.pub_annotated_compressed = self.create_publisher(CompressedImage, '/yolo/image_annotated/compressed', 1)
            self.encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 75]
            self.get_logger().info(">>> [YOLO11n] 시각화 영상 발행 모드 활성화 (/yolo/image_annotated)")
        else:
            self.pub_annotated = None
            self.pub_annotated_compressed = None
            self.get_logger().info(">>> [YOLO11n] 영상 재인코딩 비활성화 모드 (초경량 JSON 단독 발행으로 최대 20ms 지연 절감)")

        # 3. 토픽 서브스크라이버 (라즈베리파이 실시간 영상 수신)
        if use_compressed:
            sub_topic = '/camera/image_raw/compressed'
            msg_type = CompressedImage
        else:
            sub_topic = '/camera/image_raw'
            msg_type = Image

        self.sub_cam = self.create_subscription(
            msg_type,
            sub_topic,
            self.image_callback,
            1
        )
        
        self.zone_enable = True
        self.sub_zone_enable = self.create_subscription(
            Bool,
            '/yolo/zone_enable',
            self.zone_enable_callback,
            10
        )
        self.zone_hb_timer = self.create_timer(1.0, self._zone_heartbeat_callback)

        # Jetson 실시간 시스템 텔레메트리 발행 (/jetson/system_state, 2초 주기, /proc/meminfo 실측)
        self.pub_sys_state = self.create_publisher(String, '/jetson/system_state', 5)
        self.sys_state_timer = self.create_timer(2.0, self._publish_system_state)
        self._publish_system_state()

        self.processing = False
        self.get_logger().info(f">>> [YOLO11n] {sub_topic} 수신 대기 중...")

    def _publish_system_state(self):
        """Jetson 통합 RAM · Swap · 온도 실측치를 /jetson/system_state JSON으로 발행."""
        try:
            mem = {}
            with open('/proc/meminfo', 'r', encoding='utf-8') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        mem[parts[0].rstrip(':')] = int(parts[1])  # kB
            total_kb = mem.get('MemTotal', 0)
            avail_kb = mem.get('MemAvailable', mem.get('MemFree', 0))
            used_kb = max(0, total_kb - avail_kb)
            swap_total_kb = mem.get('SwapTotal', 0)
            swap_free_kb = mem.get('SwapFree', 0)
            swap_used_kb = max(0, swap_total_kb - swap_free_kb)

            ram_total_gb = round(total_kb / (1024.0 * 1024.0), 1)
            ram_used_gb = round(used_kb / (1024.0 * 1024.0), 1)
            ram_pct = round((used_kb / total_kb) * 100.0, 1) if total_kb > 0 else 0.0
            swap_total_gb = round(swap_total_kb / (1024.0 * 1024.0), 1)
            swap_used_mb = int(round(swap_used_kb / 1024.0))

            temp_c = None
            for tz in range(8):
                tpath = f'/sys/devices/virtual/thermal/thermal_zone{tz}/temp'
                npath = f'/sys/devices/virtual/thermal/thermal_zone{tz}/type'
                if os.path.exists(tpath) and os.path.exists(npath):
                    tname = open(npath, 'r', encoding='utf-8').read().strip().lower()
                    if 'cpu' in tname or 'gpu' in tname or 'tj' in tname:
                        val = int(open(tpath, 'r', encoding='utf-8').read().strip())
                        c = round(val / 1000.0, 1) if val > 200 else float(val)
                        if temp_c is None or c > temp_c:
                            temp_c = c

            msg = String()
            msg.data = json.dumps({
                'ram_total_gb': ram_total_gb,
                'ram_used_gb': ram_used_gb,
                'ram_pct': ram_pct,
                'swap_total_gb': swap_total_gb,
                'swap_used_mb': swap_used_mb,
                'temp_c': temp_c,
            })
            self.pub_sys_state.publish(msg)
        except Exception:
            pass

    def _zone_heartbeat_callback(self):
        if not self.zone_enable:
            empty_msg = String()
            empty_msg.data = json.dumps({"timestamp": 0.0, "image_size": [640, 480], "count": 0, "objects": [], "zone_paused": True})
            self.pub_detections.publish(empty_msg)

    def zone_enable_callback(self, msg: Bool):
        new_state = bool(msg.data)
        if new_state != self.zone_enable:
            self.zone_enable = new_state
            self.get_logger().info(f">>> [YOLO11n Zone] 사람탐지 구역 상태 전환: {'ON' if new_state else 'OFF'}")
            if not new_state:
                self._zone_heartbeat_callback()

    def image_callback(self, msg):
        if not self.zone_enable:
            return
        if self.processing:
            return
        self.processing = True

        try:
            if isinstance(msg, CompressedImage):
                frame = cv2.imdecode(np.frombuffer(msg.data, dtype=np.uint8), cv2.IMREAD_COLOR)
            else:
                frame = np.frombuffer(msg.data, dtype=np.uint8).reshape((msg.height, msg.width, 3))

            if frame is None:
                return

            h, w, _ = frame.shape

            results = self.model(
                frame,
                device=self.device,
                imgsz=self.imgsz,
                conf=self.conf,
                verbose=False
            )

            header_sec = getattr(msg.header.stamp, 'sec', 0)
            header_nanosec = getattr(msg.header.stamp, 'nanosec', 0)
            stamp_float = header_sec + (header_nanosec * 1e-9)

            detection_payload = {
                "timestamp": round(stamp_float, 4),
                "frame_id": getattr(msg.header, 'frame_id', 'camera_frame'),
                "image_size": [w, h],
                "count": 0,
                "objects": []
            }

            boxes = results[0].boxes
            for box in boxes:
                cls_id = int(box.cls[0])
                name = self.model.names[cls_id]
                conf = float(box.conf[0])
                xyxy = box.xyxy[0].tolist()
                
                x1, y1, x2, y2 = [round(float(v), 1) for v in xyxy]
                bw = round(x2 - x1, 1)
                bh = round(y2 - y1, 1)
                cx = round(x1 + bw / 2.0, 1)
                cy = round(y1 + bh / 2.0, 1)

                obj_info = {
                    "class_id": cls_id,
                    "class_name": name,
                    "conf": round(conf, 3),
                    "box": [x1, y1, x2, y2],
                    "center": [cx, cy],
                    "size": [bw, bh],
                    "rel_box": [
                        round(x1 / w, 4), round(y1 / h, 4),
                        round(x2 / w, 4), round(y2 / h, 4)
                    ]
                }
                detection_payload["objects"].append(obj_info)

            detection_payload["count"] = len(detection_payload["objects"])

            det_msg = String()
            det_msg.data = json.dumps(detection_payload, ensure_ascii=False)
            self.pub_detections.publish(det_msg)

            if self.publish_annotated and self.pub_annotated is not None:
                annotated_frame = results[0].plot()

                out_msg = Image()
                out_msg.header = msg.header
                out_msg.height, out_msg.width, _ = annotated_frame.shape
                out_msg.encoding = 'bgr8'
                out_msg.is_bigendian = False
                out_msg.step = out_msg.width * 3
                out_msg.data = annotated_frame.tobytes()
                self.pub_annotated.publish(out_msg)

                _, enc = cv2.imencode('.jpg', annotated_frame, self.encode_params)
                comp_msg = CompressedImage()
                comp_msg.header = msg.header
                comp_msg.format = 'jpeg'
                comp_msg.data = enc.tobytes()
                self.pub_annotated_compressed.publish(comp_msg)

        except Exception as e:
            self.get_logger().error(f"YOLO 추론 에러: {e}")
        finally:
            self.processing = False


def main(args=None):
    rclpy.init(args=args)
    node = JetsonYoloNode()
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

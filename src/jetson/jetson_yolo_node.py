#!/usr/bin/env python3
"""
[젯슨 나노/오린 전용] 실시간 ROS 2 YOLO11/YOLOv8 초경량 객체 인식 노드
- 0.1ms JSON 바운딩 박스/좌표 경량 발행 (/yolo/detections)
- 영상 재인코딩(plot/imencode) On/Off 스위치 탑재로 추론 지연 및 대역폭 최소화
"""
import json
import sys
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
        super().__init__('jetson_yolo_node')

        # 1. ROS 2 파라미터 선언
        # - model: 사용할 YOLO 가중치 파일 (yolo11n.pt 권장: 300MB 점유 및 3~5ms 초고속 추론)
        # - imgsz: 추론 해상도 (480 또는 640 권장)
        # - conf: 객체 검출 신뢰도 임계값
        # - use_compressed: 카메라 원본을 CompressedImage로 수신할지 여부
        # - publish_annotated: 시각화 영상(plot + JPEG 인코딩) 발행 여부 (False 권장: 0.1ms JSON만 발행하여 20ms 지연 및 대역폭 제거)
        self.declare_parameter('model', 'yolo11n.pt')
        self.declare_parameter('imgsz', 480)
        self.declare_parameter('conf', 0.35)
        self.declare_parameter('use_compressed', True)
        self.declare_parameter('publish_annotated', False)

        model_name = self.get_parameter('model').get_parameter_value().string_value
        self.imgsz = self.get_parameter('imgsz').get_parameter_value().integer_value
        self.conf = self.get_parameter('conf').get_parameter_value().double_value
        use_compressed = self.get_parameter('use_compressed').get_parameter_value().bool_value
        self.publish_annotated = self.get_parameter('publish_annotated').get_parameter_value().bool_value

        # GPU(CUDA) 가속 확인 및 모델 로드
        device = '0' if torch.cuda.is_available() else 'cpu'
        self.get_logger().info(
            f">>> [YOLO] 모델 로딩 중: {model_name} (디바이스: {device}, 해상도: {self.imgsz}, "
            f"임계값: {self.conf}, 시각화영상발행: {self.publish_annotated})"
        )
        
        # YOLO11 모델 로드 (Orin Nano Ampere GPU 최적화)
        self.model = YOLO(model_name)
        self.device = device
        self.get_logger().info(f">>> [YOLO] {model_name} 모델 로드 완료!")

        # 2. 토픽 퍼블리셔
        # (1) 초경량 JSON 객체 검출 데이터 (/yolo/detections, String)
        # 클래스, 신뢰도, 픽셀 좌표(xyxy), 정규화 좌표, 중심점(center), 크기(wh) 포함
        self.pub_detections = self.create_publisher(String, '/yolo/detections', 10)

        # (2) 시각화 영상 토픽 (publish_annotated=True 일 때만 실제 인코딩/발행)
        if self.publish_annotated:
            self.pub_annotated = self.create_publisher(Image, '/yolo/image_annotated', 1)
            self.pub_annotated_compressed = self.create_publisher(CompressedImage, '/yolo/image_annotated/compressed', 1)
            self.encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 75]
        else:
            self.pub_annotated = None
            self.pub_annotated_compressed = None
            self.get_logger().info(">>> [YOLO] 영상 재인코딩 비활성화 모드 (초경량 JSON 단독 발행으로 최대 20ms 지연 절감)")

        # 3. 토픽 서브스크라이버 (라즈베리파이 실시간 영상 수신)
        # Wi-Fi 220Mbps 폭격을 막고 30 FPS를 확보하기 위해 기본값으로 압축 영상 수신
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

        self.processing = False
        self.get_logger().info(f">>> [YOLO Node] {sub_topic} 수신 대기 중 (초경량 JSON 좌표 모드 준비 완료)...")

    def _zone_heartbeat_callback(self):
        if not self.zone_enable:
            empty_msg = String()
            empty_msg.data = json.dumps({"timestamp": 0.0, "image_size": [640, 480], "count": 0, "objects": [], "zone_paused": True})
            self.pub_detections.publish(empty_msg)

    def zone_enable_callback(self, msg: Bool):
        new_state = bool(msg.data)
        if new_state != self.zone_enable:
            self.zone_enable = new_state
            self.get_logger().info(f">>> [YOLO Zone] 사람탐지 구역 상태 전환: {'ON' if new_state else 'OFF'}")
            if not new_state:
                self._zone_heartbeat_callback()

    def image_callback(self, msg):
        # 구역 외 비활성화 상태면 추론 스킵 (GPU/전력 절약)
        if not self.zone_enable:
            return
        # 지연 시간(Lag) 방지: 이전 프레임 추론 중이면 새 프레임 스킵
        if self.processing:
            return
        self.processing = True

        try:
            # ROS 2 Image / CompressedImage -> OpenCV BGR 변환
            if isinstance(msg, CompressedImage):
                frame = cv2.imdecode(np.frombuffer(msg.data, dtype=np.uint8), cv2.IMREAD_COLOR)
            else:
                frame = np.frombuffer(msg.data, dtype=np.uint8).reshape((msg.height, msg.width, 3))

            if frame is None:
                return

            h, w, _ = frame.shape

            # YOLO 객체 인식 추론 (기본 imgsz=480, conf=0.35)
            results = self.model(
                frame,
                device=self.device,
                imgsz=self.imgsz,
                conf=self.conf,
                verbose=False
            )

            # JSON 페이로드 구조화
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
                xyxy = box.xyxy[0].tolist()  # [x1, y1, x2, y2]
                
                x1, y1, x2, y2 = [round(float(v), 1) for v in xyxy]
                bw = round(x2 - x1, 1)
                bh = round(y2 - y1, 1)
                cx = round(x1 + bw / 2.0, 1)
                cy = round(y1 + bh / 2.0, 1)

                obj_info = {
                    "class_id": cls_id,
                    "class_name": name,
                    "conf": round(conf, 3),
                    "box": [x1, y1, x2, y2],          # 픽셀 좌표 [x1, y1, x2, y2]
                    "center": [cx, cy],               # 중심점 [cx, cy]
                    "size": [bw, bh],                 # 박스 크기 [w, h]
                    "rel_box": [                      # 정규화 좌표 0.0 ~ 1.0 (제어/추종 연산에 용이)
                        round(x1 / w, 4), round(y1 / h, 4),
                        round(x2 / w, 4), round(y2 / h, 4)
                    ]
                }
                detection_payload["objects"].append(obj_info)

            detection_payload["count"] = len(detection_payload["objects"])

            # 초경량 JSON 문자열 발행
            det_msg = String()
            det_msg.data = json.dumps(detection_payload, ensure_ascii=False)
            self.pub_detections.publish(det_msg)

            # 시각화 영상 발행 (옵션 활성화 시에만 렌더링/인코딩)
            if self.publish_annotated and self.pub_annotated is not None:
                annotated_frame = results[0].plot()

                # Raw 토픽
                out_msg = Image()
                out_msg.header = msg.header
                out_msg.height, out_msg.width, _ = annotated_frame.shape
                out_msg.encoding = 'bgr8'
                out_msg.is_bigendian = False
                out_msg.step = out_msg.width * 3
                out_msg.data = annotated_frame.tobytes()
                self.pub_annotated.publish(out_msg)

                # 초저지연 압축 토픽
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

#!/usr/bin/env python3
"""
[초저지연 + 저전력 안정 + 선명도 최적화] 라즈베리파이 5 CSI 카메라 ROS 2 스트리머
- jta_camera 정식 패키지 노드
- ROS 2 파라미터 지원: width, height, jpeg_quality, flip_h, flip_v
"""
import glob
import os
import subprocess
import time
import numpy as np
import cv2

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CompressedImage, CameraInfo

SENSOR_W = 1640
SENSOR_H = 1232


def get_media_device():
    for dev in sorted(glob.glob('/dev/media*')):
        try:
            out = subprocess.check_output(['media-ctl', '-d', dev, '-p'], text=True, stderr=subprocess.DEVNULL)
            if 'rp1-cfe' in out and 'imx219' in out:
                return dev
        except Exception:
            pass
    return '/dev/media0'


def get_imx219_subdev():
    for dev in sorted(glob.glob('/sys/class/video4linux/v4l-subdev*')):
        try:
            with open(f"{dev}/name", "r") as f:
                if "imx219" in f.read():
                    return f"/dev/{os.path.basename(dev)}"
        except Exception:
            pass
    return '/dev/v4l-subdev2'


class LowLatencyCameraNode(Node):
    def __init__(self):
        super().__init__('camera_publisher_node')

        # 파라미터 선언 (기본값 설정 및 동적 설정 가능)
        self.declare_parameter('width', 640)
        self.declare_parameter('height', 480)
        self.declare_parameter('jpeg_quality', 90)
        self.declare_parameter('target_fps', 15)
        self.declare_parameter('flip_h', 1)
        self.declare_parameter('flip_v', 1)

        self.width = self.get_parameter('width').get_parameter_value().integer_value
        self.height = self.get_parameter('height').get_parameter_value().integer_value
        self.jpeg_quality = self.get_parameter('jpeg_quality').get_parameter_value().integer_value
        self.target_fps = self.get_parameter('target_fps').get_parameter_value().integer_value
        self.flip_h = self.get_parameter('flip_h').get_parameter_value().integer_value
        self.flip_v = self.get_parameter('flip_v').get_parameter_value().integer_value

        self.pub_raw = self.create_publisher(Image, '/camera/image_raw', 1)
        self.pub_comp = self.create_publisher(CompressedImage, '/camera/image_raw/compressed', 1)
        self.pub_info = self.create_publisher(CameraInfo, '/camera/camera_info', 1)

        self.get_logger().info(f'>>> [jta_camera] {self.width}x{self.height} (Q:{self.jpeg_quality}, Target FPS:{self.target_fps}) 스트리머 준비 완료!')

    def setup_camera(self):
        subprocess.run(['pkill', '-9', '-f', 'v4l2-ctl.*video0'], check=False, stderr=subprocess.DEVNULL)

        media_dev = get_media_device()
        subdev = get_imx219_subdev()

        # 1. 하드웨어 회전 및 노출/게인 설정
        hw_ctrl_cmds = [
            ['v4l2-ctl', '-d', subdev, f'--set-ctrl=horizontal_flip={self.flip_h},vertical_flip={self.flip_v}'],
            ['v4l2-ctl', '-d', subdev, '--set-ctrl=exposure=1400,analogue_gain=160'],
        ]
        for cmd in hw_ctrl_cmds:
            subprocess.run(cmd, check=False)

        # 2. 풀 화각(Full FOV) 1640x1232 8-bit RAW 파이프라인
        pipe_cmds = [
            ['media-ctl', '-d', media_dev, '-r'],
            ['media-ctl', '-d', media_dev, '-l', '"csi2":4 -> "rp1-cfe-csi2_ch0":0 [1]'],
            ['media-ctl', '-d', media_dev, '-V', f'"imx219 10-0010":0 [fmt:SBGGR8_1X8/{SENSOR_W}x{SENSOR_H} field:none]'],
            ['media-ctl', '-d', media_dev, '-V', f'"csi2":0 [fmt:SBGGR8_1X8/{SENSOR_W}x{SENSOR_H} field:none]'],
            ['media-ctl', '-d', media_dev, '-V', f'"csi2":4 [fmt:SBGGR8_1X8/{SENSOR_W}x{SENSOR_H} field:none]'],
            ['v4l2-ctl', '-d', '/dev/video0', f'--set-fmt-video=width={SENSOR_W},height={SENSOR_H},pixelformat=BA81'],
        ]
        for cmd in pipe_cmds:
            subprocess.run(cmd, check=False)

        stride = SENSOR_W
        try:
            out = subprocess.check_output(['v4l2-ctl', '-d', '/dev/video0', '--get-fmt-video'], text=True)
            for line in out.splitlines():
                if 'Bytes per Line' in line:
                    stride = int(line.split(':')[1].strip())
        except Exception:
            stride = SENSOR_W

        return stride

    def run_stream(self):
        stride = self.setup_camera()
        frame_bytes = SENSOR_H * stride

        lut_b = np.clip(np.arange(256) * 1.30, 0, 255).astype(np.uint8)
        lut_g = np.arange(256, dtype=np.uint8)
        lut_r = np.clip(np.arange(256) * 1.55, 0, 255).astype(np.uint8)
        wb_lut = np.dstack((lut_b, lut_g, lut_r))

        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]

        cmd = [
            'v4l2-ctl', '-d', '/dev/video0',
            '--stream-mmap=4',
            '--stream-to=-'
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=frame_bytes
        )

        msg_raw = Image()
        msg_raw.header.frame_id = 'camera_link'
        msg_raw.height = self.height
        msg_raw.width = self.width
        msg_raw.encoding = 'bgr8'
        msg_raw.is_bigendian = False
        msg_raw.step = self.width * 3

        msg_comp = CompressedImage()
        msg_comp.header.frame_id = 'camera_link'
        msg_comp.format = 'jpeg'

        msg_info = CameraInfo()
        msg_info.header.frame_id = 'camera_link'
        msg_info.width = self.width
        msg_info.height = self.height

        min_interval = 1.0 / max(1, self.target_fps)
        last_pub_sec = 0.0

        try:
            while rclpy.ok():
                raw_bytes = proc.stdout.read(frame_bytes)
                if len(raw_bytes) < frame_bytes:
                    break

                now_sec = time.time()
                # 타겟 FPS 주기가 도래하지 않았으면 가벼운 프레임 드롭 (파이프 버퍼링 지연 방지 & CPU 쿨링)
                if (now_sec - last_pub_sec) < (min_interval * 0.90):
                    continue

                last_pub_sec = now_sec
                now_stamp = self.get_clock().now().to_msg()

                raw = np.frombuffer(raw_bytes, dtype=np.uint8).reshape((SENSOR_H, stride))[:, :SENSOR_W]
                bgr = cv2.cvtColor(raw, cv2.COLOR_BAYER_BG2BGR)
                resized = cv2.resize(bgr, (self.width, self.height), interpolation=cv2.INTER_LINEAR)
                balanced = cv2.LUT(resized, wb_lut)

                # 아무도 안 보는 비압축 RAW(1MB)는 구독자가 있을 때만 On-Demand 발행 (메모리/네트워크 절약)
                if self.pub_raw.get_subscription_count() > 0:
                    msg_raw.header.stamp = now_stamp
                    msg_raw.data = balanced.tobytes()
                    self.pub_raw.publish(msg_raw)

                # 메인 스트림: 압축 JPEG 인코딩 및 발행 (YOLO, VLM, 관제 WebUI 연동)
                _, enc = cv2.imencode('.jpg', balanced, encode_params)
                msg_comp.header.stamp = now_stamp
                msg_comp.data = enc.tobytes()
                self.pub_comp.publish(msg_comp)

                if self.pub_info.get_subscription_count() > 0:
                    msg_info.header.stamp = now_stamp
                    self.pub_info.publish(msg_info)

        except Exception as e:
            if rclpy.ok():
                self.get_logger().error(f"Stream error: {e}")
        finally:
            proc.terminate()
            proc.wait()


def main(args=None):
    rclpy.init(args=args)
    node = LowLatencyCameraNode()
    try:
        node.run_stream()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()

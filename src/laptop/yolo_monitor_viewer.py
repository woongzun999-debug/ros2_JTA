#!/usr/bin/env python3
"""
[노트북/Host 관제 전용] 실시간 초경량 YOLO 객체 모니터링 뷰어
- Mint 호스트에서 실행 시 Docker 컨테이너(ros2_humble_basic)를 자동 감지/재실행
- Pi 5의 카메라 압축 스트림(/camera/image_raw/compressed)과
  젯슨의 초경량 JSON 좌표(/yolo/detections)를 수신하여 노트북에서 실시간 오버레이
- 젯슨의 연산 부담(plot/imencode 20ms) 및 Wi-Fi 대역폭 폭격 제로화 달성
"""

import os
import sys
import subprocess

# ---------------------------------------------------------------------------
# [스마트 래퍼 로직] Mint 호스트 환경 자동 감지 및 Docker 컨테이너 재호출
# 호스트(Mint)에서 rclpy가 없으면 스스로 docker exec를 호출하여 컨테이너 내부에서 구동
# ---------------------------------------------------------------------------
def _check_and_reexec_in_docker():
    # 1. 이미 도커 컨테이너 내부이거나 환경변수가 셋업되어 rclpy 임포트 가능하면 통과
    try:
        import rclpy
        return
    except ImportError:
        pass

    # 2. 호스트 환경으로 판별됨: Docker 실행 처리
    container_name = "ros2_humble_basic"
    print(f"\n[Host Detect] 호스트 Mint 환경 감지됨 (ROS 2 없음).")
    print(f"[Host Detect] Docker 컨테이너 '{container_name}' 환경으로 자동 진입하여 실행합니다...\n")

    # X11 권한 자동 허용
    try:
        subprocess.run(["xhost", "+local:root"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    display_env = os.environ.get("DISPLAY", ":0")

    # 컨테이너 실행 상태 확인 (없으면 ros2basic_nonNV.sh 기반으로 백그라운드 안전 기동)
    check_cmd = ["docker", "inspect", "-f", "{{.State.Running}}", container_name]
    res = subprocess.run(check_cmd, capture_output=True, text=True)
    if res.returncode != 0 or res.stdout.strip() != "true":
        print(f"⚠️  Docker 컨테이너 '{container_name}'가 실행 중이 아닙니다.")
        print("💡 내장 그래픽(iGPU) 안전 모드로 컨테이너를 기동합니다...")
        # 기존 죽은 컨테이너 정리
        subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        run_docker_cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "--net=host",
            "--ipc=host",
            "--privileged",
            "-e", f"DISPLAY={display_env}",
            "-e", "QT_X11_NO_MITSHM=1",
            "-e", "ROS_DOMAIN_ID=30",
            "-e", "RMW_IMPLEMENTATION=rmw_cyclonedds_cpp",
            "-e", "CYCLONEDDS_URI=file:///root/ros2_JTA/config/cyclonedds.xml",
            "-e", "TURTLEBOT3_MODEL=waffle",
            "-v", "/tmp/.X11-unix:/tmp/.X11-unix:rw",
            f"-v", f"{os.path.expanduser('~')}/.Xauthority:/root/.Xauthority:rw",
            "-v", f"{os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))}:/root/ros2_JTA:rw",
            "-v", "/etc/localtime:/etc/localtime:ro",
            "--device", "/dev/dri:/dev/dri",
            "-w", "/root/ros2_JTA",
            "ros2-humble-gazebo:latest",
            "sleep", "infinity"
        ]
        subprocess.run(run_docker_cmd, check=True)

    # 컨테이너 내부 경로: /root/ros2_JTA/src/laptop/yolo_monitor_viewer.py
    # 패스스루 인자(sys.argv[1:]) 전달
    args_str = " ".join([f'"{a}"' for a in sys.argv[1:]])
    inner_cmd = (
        f"source /opt/ros/humble/setup.bash && "
        f"python3 /root/ros2_JTA/src/laptop/yolo_monitor_viewer.py {args_str}"
    )

    docker_exec_cmd = [
        "docker", "exec", "-it",
        "-e", f"DISPLAY={display_env}",
        container_name,
        "bash", "-c", inner_cmd
    ]

    try:
        sys.exit(subprocess.call(docker_exec_cmd))
    except KeyboardInterrupt:
        sys.exit(0)

# 최상단에서 도커 래퍼 체크 실행
_check_and_reexec_in_docker()

# ---------------------------------------------------------------------------
# 이하 ROS 2 및 GUI 모니터링 본체 (도커 컨테이너 내부에서 실행됨)
# ---------------------------------------------------------------------------
import json
import time
import cv2
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import CompressedImage, BatteryState
from std_msgs.msg import String


class YoloMonitorViewer(Node):
    def __init__(self):
        super().__init__('yolo_monitor_viewer')

        # 1. 파라미터 선언
        self.declare_parameter('image_topic', '/camera/image_raw/compressed')
        self.declare_parameter('json_topic', '/yolo/detections')
        self.declare_parameter('battery_topic', '/battery_state')
        self.declare_parameter('pi5_battery_topic', '/pi5/battery_state')
        self.declare_parameter('window_name', 'JTA Bot - YOLO Ground Station Monitor')

        image_topic = self.get_parameter('image_topic').get_parameter_value().string_value
        json_topic = self.get_parameter('json_topic').get_parameter_value().string_value
        battery_topic = self.get_parameter('battery_topic').get_parameter_value().string_value
        pi5_battery_topic = self.get_parameter('pi5_battery_topic').get_parameter_value().string_value
        self.window_name = self.get_parameter('window_name').get_parameter_value().string_value

        # 2. 통신 QoS 설정 (초저지연 Real-time Best-Effort / 큐 깊이 1)
        qos_realtime = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        # 3. 최신 프레임 및 최신 JSON, 배터리 저장 버퍼 (스레드 세이프 단순 캐시)
        self.latest_frame = None
        self.latest_detections = None
        self.frame_timestamp = 0.0
        self.json_timestamp = 0.0

        # OpenCR 배터리 상태 (기본값 None)
        self.battery_voltage = None
        self.battery_percentage = None
        self.last_battery_time = 0.0

        # 성능 통계 측정용
        self.fps_counter = 0
        self.fps_start_time = time.time()
        self.current_fps = 0.0

        # 4. 서브스크라이버 등록
        self.sub_cam = self.create_subscription(
            CompressedImage,
            image_topic,
            self.image_callback,
            qos_realtime
        )

        self.sub_json = self.create_subscription(
            String,
            json_topic,
            self.json_callback,
            10
        )

        self.sub_battery = self.create_subscription(
            BatteryState,
            battery_topic,
            self.battery_callback,
            qos_realtime
        )
        self.pi5_voltage = None
        self.pi5_pct = None
        self.pi5_temp = None
        self.last_pi5_time = 0.0
        self.sub_pi5_battery = self.create_subscription(
            BatteryState,
            pi5_battery_topic,
            self.pi5_battery_callback,
            qos_realtime
        )

        # 5. UI 렌더링 타이머 (30 FPS 주기로 GUI 갱신, 약 33ms)
        self.render_timer = self.create_timer(0.033, self.render_loop)

        # 클래스별 고유 색상 팔레트 캐시 (BGR)
        self.color_palette = [
            (0, 255, 0),    # Green
            (255, 128, 0),  # Skyblue
            (0, 165, 255),  # Orange
            (0, 0, 255),    # Red
            (255, 0, 255),  # Magenta
            (255, 255, 0),  # Cyan
            (180, 105, 255) # Pink
        ]

        # 창 크기 자유 조절 가능 설정 (WINDOW_NORMAL)
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.window_name, 960, 720)

        self.get_logger().info("==================================================")
        self.get_logger().info("🚀 [Host Monitor] 실시간 관제 뷰어 기동 완료!")
        self.get_logger().info(f" - 영상 토픽: {image_topic}")
        self.get_logger().info(f" - JSON 토픽: {json_topic}")
        self.get_logger().info(f" - 배터리 토픽: {battery_topic}")
        self.get_logger().info(" - 조작: [Q] 종료 | [S] 프레임 스크린샷 저장")
        self.get_logger().info("==================================================")

    def image_callback(self, msg: CompressedImage):
        """Pi 5 카메라 실시간 프레임 디코딩"""
        try:
            np_arr = np.frombuffer(msg.data, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if frame is not None:
                self.latest_frame = frame
                # 헤더 타임스탬프 계산
                sec = getattr(msg.header.stamp, 'sec', 0)
                nanosec = getattr(msg.header.stamp, 'nanosec', 0)
                self.frame_timestamp = sec + (nanosec * 1e-9)
        except Exception as e:
            self.get_logger().warn(f"영상 디코딩 에러: {e}")

    def json_callback(self, msg: String):
        """젯슨 Orin Nano에서 수신된 경량 JSON 좌표 파싱"""
        try:
            data = json.loads(msg.data)
            self.latest_detections = data
            self.json_timestamp = data.get("timestamp", 0.0)
        except Exception as e:
            self.get_logger().warn(f"JSON 파싱 에러: {e}")

    def pi5_battery_callback(self, msg: BatteryState):
        """라즈베리파이 5 PD 보조배터리 전압 및 퍼센트, 온도 수신"""
        self.pi5_voltage = msg.voltage
        self.pi5_pct = msg.percentage * 100.0 if msg.percentage <= 1.0 else msg.percentage
        self.pi5_temp = msg.temperature
        self.last_pi5_time = time.time()

    def battery_callback(self, msg: BatteryState):
        """OpenCR 배터리 전압 및 퍼센트 수신"""
        self.battery_voltage = msg.voltage
        # 배터리 퍼센트가 0.0~1.0 사이로 제공되지 않는 경우 전압 기반(3S LiPo: 10.5V ~ 12.6V) 계산
        if msg.percentage > 0.0:
            self.battery_percentage = min(100.0, max(0.0, msg.percentage * 100.0))
        elif self.battery_voltage is not None and self.battery_voltage > 0.0:
            # 3S 기준 (10.5V: 0%, 12.6V: 100%)
            pct = ((self.battery_voltage - 10.5) / (12.6 - 10.5)) * 100.0
            self.battery_percentage = min(100.0, max(0.0, pct))
        self.last_battery_time = time.time()

    def render_loop(self):
        """노트북 화면에 바운딩 박스 오버레이 및 렌더링"""
        if self.latest_frame is None:
            return

        # 원본 보호를 위해 복사본 생성
        canvas = self.latest_frame.copy()
        h, w, _ = canvas.shape

        # FPS 연산
        self.fps_counter += 1
        now = time.time()
        elapsed = now - self.fps_start_time
        if elapsed >= 1.0:
            self.current_fps = self.fps_counter / elapsed
            self.fps_counter = 0
            self.fps_start_time = now

        # 검출 객체 박스 및 레이블 드로잉
        det_count = 0
        if self.latest_detections and "objects" in self.latest_detections:
            objects = self.latest_detections.get("objects", [])
            det_count = len(objects)

            for obj in objects:
                cls_id = obj.get("class_id", 0)
                cls_name = obj.get("class_name", "object")
                conf = obj.get("conf", 0.0)
                box = obj.get("box", [0, 0, 0, 0])
                center = obj.get("center", [0, 0])

                x1, y1, x2, y2 = [int(v) for v in box]
                cx, cy = [int(v) for v in center]

                color = self.color_palette[cls_id % len(self.color_palette)]

                # 1) 바운딩 박스 그리기
                cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)

                # 2) 객체 중심점 십자선 (타겟팅/추종 시각화)
                cv2.drawMarker(canvas, (cx, cy), color, markerType=cv2.MARKER_CROSS, markerSize=12, thickness=2)

                # 3) 상단 라벨 텍스트 배경 박스 및 텍스트
                label_text = f"{cls_name} {conf:.2f}"
                (txt_w, txt_h), baseline = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                
                label_y1 = max(0, y1 - txt_h - 6)
                cv2.rectangle(canvas, (x1, label_y1), (x1 + txt_w + 6, label_y1 + txt_h + 6), color, -1)
                cv2.putText(
                    canvas,
                    label_text,
                    (x1 + 3, label_y1 + txt_h + 2),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 0, 0),
                    1,
                    cv2.LINE_AA
                )

        # 4) 좌상단 관제 HUD 대시보드 오버레이 (컴팩트 절반 사이즈 + Pi5 & OpenCR 듀얼 배터리)
        # 반투명 배경 패널 (가로 250px, 높이 78px - 기존 대비 컴팩트 슬림화)
        overlay = canvas.copy()
        cv2.rectangle(overlay, (8, 8), (255, 84), (15, 15, 15), -1)
        cv2.addWeighted(overlay, 0.70, canvas, 0.30, 0, canvas)

        # 상태 텍스트 (폰트 0.38~0.42의 슬림 미니멀 스타일)
        latency_ms = 0.0
        if self.frame_timestamp > 0 and self.json_timestamp > 0:
            latency_ms = abs(self.frame_timestamp - self.json_timestamp) * 1000.0

        # Line 1: FPS / 해상도 / YOLO 레이턴시
        cv2.putText(canvas, f"{self.current_fps:.1f} FPS | {w}x{h} | Sync {latency_ms:.1f}ms", (14, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 255), 1, cv2.LINE_AA)

        # Line 2: 객체 검출 수
        cv2.putText(canvas, f"YOLO Detections: {det_count} objs", (14, 36),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (220, 220, 220), 1, cv2.LINE_AA)

        bat_now = time.time()

        # Line 3: OpenCR 모터 배터리 (3S LiPo)
        if self.battery_voltage is not None and (bat_now - self.last_battery_time < 5.0):
            ov = self.battery_voltage
            opct = self.battery_percentage if self.battery_percentage is not None else 0.0
            ocolor = (0, 255, 0) if ov >= 11.5 else ((0, 165, 255) if ov >= 11.1 else (0, 0, 255))
            ostr = f"OpenCR: {ov:.2f}V ({opct:.0f}%)"
            cv2.putText(canvas, ostr, (14, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.38, ocolor, 1, cv2.LINE_AA)
            # 미니 게이지 바 (가로 50px, 세로 5px)
            bx, by, bw, bh = 185, 45, 55, 6
            cv2.rectangle(canvas, (bx, by), (bx + bw, by + bh), (70, 70, 70), 1)
            ofill = int(bw * (opct / 100.0))
            if ofill > 0:
                cv2.rectangle(canvas, (bx + 1, by + 1), (bx + ofill, by + bh - 1), ocolor, -1)
        else:
            cv2.putText(canvas, "OpenCR: Disconnected", (14, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (120, 120, 120), 1, cv2.LINE_AA)

        # Line 4: Pi 5 보조배터리 / 전원 상태
        if self.pi5_voltage is not None and (bat_now - self.last_pi5_time < 5.0):
            pv = self.pi5_voltage
            ppct = self.pi5_pct if self.pi5_pct is not None else 0.0
            pcolor = (0, 255, 0) if pv >= 4.80 else ((0, 165, 255) if pv >= 4.70 else (0, 0, 255))
            temp_str = f" {self.pi5_temp:.0f}'C" if self.pi5_temp is not None else ""
            pstr = f"Pi 5:    {pv:.2f}V ({ppct:.0f}%){temp_str}"
            cv2.putText(canvas, pstr, (14, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.38, pcolor, 1, cv2.LINE_AA)
            # 미니 게이지 바 (가로 50px, 세로 5px)
            bx, by, bw, bh = 185, 63, 55, 6
            cv2.rectangle(canvas, (bx, by), (bx + bw, by + bh), (70, 70, 70), 1)
            pfill = int(bw * (ppct / 100.0))
            if pfill > 0:
                cv2.rectangle(canvas, (bx + 1, by + 1), (bx + pfill, by + bh - 1), pcolor, -1)
        else:
            cv2.putText(canvas, "Pi 5:    Disconnected", (14, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (120, 120, 120), 1, cv2.LINE_AA)

        # 창 표시
        cv2.imshow(self.window_name, canvas)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q') or key == ord('Q'):
            self.get_logger().info("사용자 요청으로 모니터링 창을 종료합니다.")
            rclpy.shutdown()
        elif key == ord('s') or key == ord('S'):
            filename = f"capture_{int(time.time())}.jpg"
            cv2.imwrite(filename, canvas)
            self.get_logger().info(f"📸 캡처 완료: {filename}")


def main(args=None):
    rclpy.init(args=args)
    node = YoloMonitorViewer()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        cv2.destroyAllWindows()
        if rclpy.ok():
            node.destroy_node()
            rclpy.shutdown()


if __name__ == '__main__':
    main()

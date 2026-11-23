#!/usr/bin/env python3
"""
TurtleBot3 부드러운 연속 주행 장애물 회피 노드 + 비상 정지(E-Stop) (ROS 2 Humble)
- 20Hz 고주기 타이머 기반 모터 제어
- [비상 정지 1] 터미널 Spacebar 키로 즉시 정지/재개 (E-Stop 토글)
- [비상 정지 2] 전방 초근접(18cm 이하) 손/장애물 감지 시 즉각 올스톱
- [비상 정지 3] 전방/좌/우 사방 포위 시 충돌 방지 정지
- 히스테리시스 및 최소 회전 시간 적용으로 채터링 방지
"""

import sys
import select
import termios
import tty
import threading
import time
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from rclpy.executors import ExternalShutdownException
from sensor_msgs.msg import LaserScan
from geometry_msgs.msg import Twist
import numpy as np

class SmoothObstacleAvoidance(Node):
    def __init__(self):
        super().__init__("smooth_obstacle_avoidance")

        # 주행 및 안전 파라미터
        self.EMERGENCY_STOP_DIST = 0.18   # 손/물체 초근접 시 즉각 비상 정지 (m)
        self.STOP_DISTANCE = 0.25         # 일반 감지 시 후진/정지 전환 거리 (m)
        self.SAFE_DISTANCE = 0.45         # 회피 시작 거리 (m)
        self.CLEAR_DISTANCE = 0.55        # 회피 완료 후 직진 복귀 거리 (m)
        self.TARGET_LINEAR_SPEED = 0.12   # 정상 전진 속도 (m/s)
        self.TARGET_ANGULAR_SPEED = 0.45  # 회전 속도 (rad/s)
        self.MIN_TURN_TIME = 0.8          # 최소 회전 유지 시간 (초)

        # 현재 상태 변수
        self.state = "FORWARD"            # "FORWARD", "TURNING", "REVERSE", "EMERGENCY_STOP", "USER_PAUSE"
        self.manual_stop = False          # 스페이스바로 토글되는 수동 비상정지
        self.turn_direction = 1.0         # +1.0: 좌회전, -1.0: 우회전
        self.turn_start_time = 0.0

        # 속도 스무딩 변수
        self.current_linear_x = 0.0
        self.current_angular_z = 0.0
        self.MAX_LINEAR_ACCEL = 0.02
        self.MAX_ANGULAR_ACCEL = 0.08

        # 센서 데이터 변수
        self.front_dist = 3.5
        self.left_dist = 3.5
        self.right_dist = 3.5
        self.scan_received = False
        self.last_log_state = ""

        # 라이다 구독 (QoS: BEST_EFFORT)
        qos_profile = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            depth=10
        )
        self.scan_sub = self.create_subscription(
            LaserScan,
            "/scan",
            self.scan_callback,
            qos_profile
        )

        # 모터 명령 발행 (20Hz)
        self.cmd_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.timer = self.create_timer(0.05, self.control_loop)

        self.get_logger().info("==================================================")
        self.get_logger().info(" TurtleBot3 장애물 회피 + 비상정지(E-Stop) 노드 시작")
        self.get_logger().info(" [조작 안내]")
        self.get_logger().info("   - [Spacebar] : 비상 정지 (Emergency Stop / Resume)")
        self.get_logger().info("   - [q]        : 노드 안전 종료")
        self.get_logger().info(f"   - [초근접 정지] : 전방 {self.EMERGENCY_STOP_DIST}m 이내 자동 올스톱")
        self.get_logger().info("==================================================")

    def get_sector_distance(self, ranges, start_angle, end_angle):
        """노이즈를 걸러낸 영역 내 유효 최소 거리 반환"""
        if start_angle <= end_angle:
            indices = range(start_angle, end_angle + 1)
        else:
            indices = list(range(start_angle, 360)) + list(range(0, end_angle + 1))

        valid_ranges = []
        for idx in indices:
            if idx < len(ranges):
                dist = ranges[idx]
                # 0.12m 이상 유효 거리만 취급
                if 0.12 <= dist <= 3.5 and not np.isnan(dist) and not np.isinf(dist):
                    valid_ranges.append(dist)

        if not valid_ranges:
            return 3.5

        valid_ranges.sort()
        if len(valid_ranges) >= 3:
            return float(np.mean(valid_ranges[:3]))
        return float(valid_ranges[0])

    def scan_callback(self, msg):
        ranges = msg.ranges
        self.front_dist = self.get_sector_distance(ranges, 335, 25)
        self.left_dist  = self.get_sector_distance(ranges, 30, 85)
        self.right_dist = self.get_sector_distance(ranges, 275, 330)
        self.scan_received = True

    def control_loop(self):
        """20Hz 주기로 주행 제어 및 비상 정지 처리"""
        if not self.scan_received:
            return

        now = time.time()
        target_linear = 0.0
        target_angular = 0.0

        # --- 1. 사용자 수동 비상 정지 (Spacebar) ---
        if self.manual_stop:
            self.state = "USER_PAUSE"
            self.current_linear_x = 0.0
            self.current_angular_z = 0.0
            cmd = Twist()
            self.cmd_pub.publish(cmd)
            self._print_log("사용자 비상 정지(PAUSE) 활성화 중...")
            return

        # --- 2. 센서 기반 비상 정지 (초근접 또는 사방 포위) ---
        if self.front_dist < self.EMERGENCY_STOP_DIST or (
            self.front_dist < 0.22 and self.left_dist < 0.25 and self.right_dist < 0.25
        ):
            self.state = "EMERGENCY_STOP"
            # 초근접 시 급제동
            self.current_linear_x = 0.0
            self.current_angular_z = 0.0
            cmd = Twist()
            self.cmd_pub.publish(cmd)
            self._print_log("⚠️ [비상 정지] 초근접 장애물 또는 사방 막힘! 안전 대기 중...")
            return

        # --- 3. 일반 상태 머신 (FSM) ---
        if self.state in ["EMERGENCY_STOP", "USER_PAUSE"]:
            # 비상정지 해제 후 안전거리 확보 시 전진 상태로 복귀
            if self.front_dist >= self.SAFE_DISTANCE:
                self.state = "FORWARD"
            else:
                self.state = "TURNING"
                self.turn_direction = 1.0 if self.left_dist >= self.right_dist else -1.0
                self.turn_start_time = now

        if self.state == "FORWARD":
            if self.front_dist < self.STOP_DISTANCE:
                self.state = "REVERSE"
                self.turn_direction = 1.0 if self.left_dist >= self.right_dist else -1.0
                self.turn_start_time = now
            elif self.front_dist < self.SAFE_DISTANCE:
                self.state = "TURNING"
                self.turn_direction = 1.0 if self.left_dist >= self.right_dist else -1.0
                self.turn_start_time = now
            else:
                target_linear = self.TARGET_LINEAR_SPEED
                target_angular = 0.0

        elif self.state == "TURNING":
            elapsed = now - self.turn_start_time
            if elapsed >= self.MIN_TURN_TIME and self.front_dist >= self.CLEAR_DISTANCE:
                self.state = "FORWARD"
                target_linear = self.TARGET_LINEAR_SPEED
                target_angular = 0.0
            else:
                target_linear = 0.0
                target_angular = self.turn_direction * self.TARGET_ANGULAR_SPEED

        elif self.state == "REVERSE":
            if self.front_dist >= (self.STOP_DISTANCE + 0.08):
                self.state = "TURNING"
                self.turn_start_time = now
            else:
                target_linear = -0.05
                target_angular = self.turn_direction * 0.25

        # 속도 가감속 (Ramping)
        diff_lin = target_linear - self.current_linear_x
        self.current_linear_x += max(-self.MAX_LINEAR_ACCEL, min(self.MAX_LINEAR_ACCEL, diff_lin))

        diff_ang = target_angular - self.current_angular_z
        self.current_angular_z += max(-self.MAX_ANGULAR_ACCEL, min(self.MAX_ANGULAR_ACCEL, diff_ang))

        # 명령 퍼블리시
        cmd = Twist()
        cmd.linear.x = self.current_linear_x
        cmd.angular.z = self.current_angular_z
        self.cmd_pub.publish(cmd)

        dir_str = "좌회전" if self.turn_direction > 0 else "우회전"
        log_msg = f"[{self.state}] 전방:{self.front_dist:.2f}m | 좌:{self.left_dist:.2f}m | 우:{self.right_dist:.2f}m ({dir_str})"
        self._print_log(log_msg)

    def _print_log(self, msg):
        if self.state != self.last_log_state:
            self.last_log_state = self.state
            self.get_logger().info(msg)

    def stop_robot(self):
        """종료 시 모터가 완전히 멈추도록 확실하게 반복 전송"""
        try:
            if rclpy.ok():
                stop_cmd = Twist()
                stop_cmd.linear.x = 0.0
                stop_cmd.angular.z = 0.0
                # 단발 전송 시 DDS 버퍼 유실 방지를 위해 0.05초 간격으로 5회 확실히 전송
                for _ in range(5):
                    self.cmd_pub.publish(stop_cmd)
                    time.sleep(0.05)
                self.get_logger().info(">>> 로봇 모터 완전 정지 완료.")
        except Exception:
            pass


def keyboard_listener(node):
    """터미널에서 비차단 방식으로 키 입력을 받아 비상 정지 토글"""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while rclpy.ok():
            rlist, _, _ = select.select([sys.stdin], [], [], 0.1)
            if rlist:
                key = sys.stdin.read(1)
                if key == ' ':
                    node.manual_stop = not node.manual_stop
                    status = "🔴 [비상 정지 발동 (E-STOP)]" if node.manual_stop else "🟢 [주행 재개 (RESUME)]"
                    print(f"\r\n\033[91m{status}\033[0m\r\n", end="", flush=True)
                elif key.lower() == 'q':
                    print("\r\n\033[93m[종료 요청 수신] 로봇을 안전하게 정지합니다...\033[0m\r\n", end="", flush=True)
                    node.manual_stop = True
                    node.stop_robot()
                    # 노드 종료 트리거
                    rclpy.shutdown()
                    break
    except Exception:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def main(args=None):
    rclpy.init(args=args)
    node = SmoothObstacleAvoidance()

    # 키보드 리스너 스레드 실행
    key_thread = threading.Thread(target=keyboard_listener, args=(node,), daemon=True)
    key_thread.start()

    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.stop_robot()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == "__main__":
    main()

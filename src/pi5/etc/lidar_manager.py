#!/usr/bin/env python3
import subprocess
import signal
import os
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool

class LidarManager(Node):
    def __init__(self):
        super().__init__('lidar_manager')
        self.sub = self.create_subscription(
            Bool,
            '/lidar_enable',
            self.lidar_callback,
            10
        )
        self.process = None
        self.get_logger().info('>>> [Lidar Manager] 준비 완료! /lidar_enable 토픽 대기 중...')

    def start_lidar(self):
        if self.process is None or self.process.poll() is not None:
            self.get_logger().info('>>> 라이다 가동 명령 수신: hlds_laser_publisher 실행')
            # 라이다 드라이버 노드 실행
            self.process = subprocess.Popen(
                ['ros2', 'run', 'hlds_laser_publisher', 'hlds_laser_publisher'],
                preexec_fn=os.setsid
            )
        else:
            self.get_logger().info('>>> 라이다가 이미 동작 중입니다.')

    def stop_lidar(self):
        if self.process is not None and self.process.poll() is None:
            self.get_logger().info('>>> 라이다 정지 명령 수신: 프로세스 종료')
            # 자식 프로세스 그룹 전체 종료
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
                self.process.wait(timeout=3)
            except Exception:
                os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
            self.process = None
            self.get_logger().info('>>> 라이다 모터 정지 완료.')
        else:
            # 혹시 외부에서 실행된 잔여 프로세스가 있을 경우 강제 정리
            os.system("killall -9 hlds_laser_publisher 2>/dev/null")
            self.process = None
            self.get_logger().info('>>> 라이다가 이미 정지 상태입니다.')

    def lidar_callback(self, msg: Bool):
        if msg.data:
            self.start_lidar()
        else:
            self.stop_lidar()

    def destroy_node(self):
        self.stop_lidar()
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = LidarManager()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()

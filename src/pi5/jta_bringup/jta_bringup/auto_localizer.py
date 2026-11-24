#!/usr/bin/env python3
"""AMCL 전역 초기 위치 자동 추정(Global Localization) 보조 노드."""

import signal
import time
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
try:
    from rclpy.signals import SignalHandlerOptions
except ImportError:
    SignalHandlerOptions = None
from std_srvs.srv import Empty
from geometry_msgs.msg import Twist, PoseWithCovarianceStamped


class AutoLocalizerNode(Node):
    """Nav2 기동 시 AMCL 전역 초기화를 수행하고 옵션에 따라 제자리 회전 수렴을 돕는 노드."""

    def __init__(self):
        super().__init__('jta_auto_localizer')
        self.declare_parameter('enable_spin', False)
        self.declare_parameter('angular_speed', 0.5)
        self.declare_parameter('max_spin_sec', 20.0)
        self.declare_parameter('cov_threshold', 0.02)

        self.enable_spin = bool(self.get_parameter('enable_spin').value)
        self.angular_speed = float(self.get_parameter('angular_speed').value)
        self.max_spin_sec = float(self.get_parameter('max_spin_sec').value)
        self.cov_threshold = float(self.get_parameter('cov_threshold').value)

        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.glob_client = self.create_client(Empty, '/reinitialize_global_localization')
        self.nomotion_client = self.create_client(Empty, '/request_nomotion_update')

        amcl_qos = QoSProfile(
            depth=5,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.latest_cov = None
        self._stop_requested = False
        self.create_subscription(
            PoseWithCovarianceStamped,
            '/amcl_pose',
            self._amcl_pose_cb,
            amcl_qos,
        )

    def _amcl_pose_cb(self, msg: PoseWithCovarianceStamped):
        cov = msg.pose.covariance
        # x(0), y(7) 공분산 합으로 수렴 정도 판단
        self.latest_cov = float(cov[0]) + float(cov[7])

    def stop_robot(self):
        """종료 및 비상 시 /cmd_vel 0.0을 5회 연속 발행하고 DDS 전송 완료까지 대기."""
        stop_msg = Twist()
        for _ in range(5):
            if rclpy.ok():
                self.cmd_pub.publish(stop_msg)
                rclpy.spin_once(self, timeout_sec=0.02)
            time.sleep(0.05)
        time.sleep(0.25)

    def run(self):
        self.get_logger().info('Waiting for /reinitialize_global_localization service...')
        while rclpy.ok() and not self._stop_requested and not self.glob_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info('Still waiting for AMCL global localization service...')

        if not rclpy.ok() or self._stop_requested:
            return

        # AMCL lifecycle active 안정화를 위해 짧게 대기 후 전역 초기화 호출
        time.sleep(1.5)
        if self._stop_requested:
            return
        future = self.glob_client.call_async(Empty.Request())
        rclpy.spin_until_future_complete(self, future, timeout_sec=5.0)
        self.get_logger().info('Global localization initialized (uniform particles spread).')

        # 회전 전 초기 라이다 스캔으로 1차 파티클 필터링 수행
        if self.nomotion_client.wait_for_service(timeout_sec=2.0):
            for _ in range(3):
                if not rclpy.ok() or self._stop_requested:
                    break
                nm_future = self.nomotion_client.call_async(Empty.Request())
                rclpy.spin_until_future_complete(self, nm_future, timeout_sec=2.0)
                time.sleep(0.35)
            self.get_logger().info('Pre-spin nomotion updates (x3) completed.')

        if not self.enable_spin or self._stop_requested:
            if not self.enable_spin:
                self.get_logger().info(
                    'auto_spin=False: 제자리 회전 없이 대기합니다. (전원선 분리 후 auto_spin:=true 사용)'
                )
            return

        self.get_logger().info(
            f'auto_spin=True: 제자리 회전으로 AMCL 수렴 시작 (최대 {self.max_spin_sec:.1f}s)...'
        )
        start_t = time.monotonic()
        twist = Twist()
        twist.angular.z = self.angular_speed

        try:
            while rclpy.ok() and not self._stop_requested and (time.monotonic() - start_t) < self.max_spin_sec:
                self.cmd_pub.publish(twist)
                rclpy.spin_once(self, timeout_sec=0.1)
                if self.latest_cov is not None and self.latest_cov <= self.cov_threshold:
                    self.get_logger().info(
                        f'AMCL 수렴 완료! (cov={self.latest_cov:.4f} <= {self.cov_threshold})'
                    )
                    break
        finally:
            self.stop_robot()
            self.get_logger().info('AutoLocalizer 정지 완료 (/cmd_vel 0.0 x5 발행).')


def main(args=None):
    if SignalHandlerOptions is not None:
        rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    else:
        rclpy.init(args=args)
    node = AutoLocalizerNode()

    if SignalHandlerOptions is not None:
        def _on_signal(signum, _frame):
            node._stop_requested = True

        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)

    try:
        node.run()
    except KeyboardInterrupt:
        pass
    finally:
        node.stop_robot()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()

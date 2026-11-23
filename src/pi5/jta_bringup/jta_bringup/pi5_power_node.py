#!/usr/bin/env python3
"""
[Pi 5 전용] 초경량 배터리/전원 모니터링 노드 (/pi5/battery_state)
- 호스트 /dev/shm/pi5_power.json 캐시를 읽어 BatteryState 토픽을 1초마다 안정적으로 퍼블리시
"""
import os
import json
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import BatteryState

class Pi5PowerPublisher(Node):
    def __init__(self):
        super().__init__('pi5_power_node')
        self.pub = self.create_publisher(BatteryState, '/pi5/battery_state', 10)
        self.shm_path = '/dev/shm/pi5_power.json'
        self.get_logger().info(">>> [Pi 5 Power] 배터리/전원 모니터링 노드 준비 완료 (/pi5/battery_state)")
        # 최초 즉시 1회 발행
        self.publish_power()
        self.timer = self.create_timer(1.0, self.publish_power)

    def publish_power(self):
        v = 5.0
        pct = 1.0
        temp = 0.0

        if os.path.exists(self.shm_path):
            try:
                with open(self.shm_path, 'r') as f:
                    data = json.load(f)
                    v = float(data.get('voltage', 5.0))
                    pct = float(data.get('percentage', 100.0)) / 100.0
                    temp = float(data.get('temperature', 0.0))
            except Exception:
                pass

        msg = BatteryState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'base_link'
        msg.voltage = float(v)
        msg.percentage = float(pct)
        msg.temperature = float(temp)
        msg.present = True
        self.pub.publish(msg)

def main(args=None):
    rclpy.init(args=args)
    node = Pi5PowerPublisher()
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

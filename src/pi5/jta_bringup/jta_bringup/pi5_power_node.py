#!/usr/bin/env python3
"""
[Pi 5 전용] 초경량 배터리/전원/쓰로틀링 모니터링 노드 (/pi5/battery_state, /pi5/system_state)
- 호스트 /dev/shm/pi5_power.json 캐시를 읽어 BatteryState 및 실측 throttled JSON을 1초마다 퍼블리시
"""
import os
import json
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import BatteryState
from std_msgs.msg import String


def format_throttled_label(raw_hex: str) -> str:
    """vcgencmd get_throttled 16진수 비트마스크를 실측 상태 문자열로 해석."""
    s = str(raw_hex or '0x0').strip()
    try:
        val = int(s, 16)
    except ValueError:
        return s
    if val == 0:
        return "0x0 (정상)"
    active_flags = []
    if val & 0x1:
        active_flags.append("저전압")
    if val & 0x2:
        active_flags.append("클록제한")
    if val & 0x4:
        active_flags.append("쓰로틀링")
    if val & 0x8:
        active_flags.append("과열제한")
    if active_flags:
        return f"{hex(val)} (🚨 현재: {'/'.join(active_flags)})"
    hist_flags = []
    if val & 0x10000:
        hist_flags.append("저전압이력")
    if val & 0x20000:
        hist_flags.append("클록제한이력")
    if val & 0x40000:
        hist_flags.append("쓰로틀링이력")
    if val & 0x80000:
        hist_flags.append("과열이력")
    if hist_flags:
        return f"{hex(val)} (⚠️ {', '.join(hist_flags)})"
    return f"{hex(val)}"


class Pi5PowerPublisher(Node):
    def __init__(self):
        super().__init__('pi5_power_node')
        self.pub = self.create_publisher(BatteryState, '/pi5/battery_state', 10)
        self.pub_sys = self.create_publisher(String, '/pi5/system_state', 10)
        self.shm_path = '/dev/shm/pi5_power.json'
        self.get_logger().info(">>> [Pi 5 Power] 배터리/전원/쓰로틀링 모니터링 노드 준비 완료 (/pi5/battery_state, /pi5/system_state)")
        # 최초 즉시 1회 발행
        self.publish_power()
        self.timer = self.create_timer(1.0, self.publish_power)

    def publish_power(self):
        v = 5.0
        pct = 1.0
        temp = 0.0
        throttled = '0x0'

        if os.path.exists(self.shm_path):
            try:
                with open(self.shm_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    v = float(data.get('voltage', 5.0))
                    pct = float(data.get('percentage', 100.0)) / 100.0
                    temp = float(data.get('temperature', 0.0))
                    throttled = str(data.get('throttled', '0x0')).strip()
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

        sys_msg = String()
        sys_msg.data = json.dumps({
            'voltage': round(float(v), 2),
            'percentage': round(float(pct) * 100.0, 1),
            'temperature': round(float(temp), 1),
            'throttled': throttled,
            'throttled_label': format_throttled_label(throttled),
        }, ensure_ascii=False)
        self.pub_sys.publish(sys_msg)

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

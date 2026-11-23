#!/bin/bash
# 로봇 즉각 강제 정지 스크립트
echo "🛑 로봇 강제 정지 명령 전송 중..."
distrobox enter humble -- bash -c "source /opt/ros/humble/setup.bash && python3 -c '
import rclpy
from geometry_msgs.msg import Twist
import time
rclpy.init()
node = rclpy.create_node(\"emergency_killer\")
pub = node.create_publisher(Twist, \"/cmd_vel\", 10)
stop = Twist()
for _ in range(10):
    pub.publish(stop)
    time.sleep(0.05)
rclpy.shutdown()
'"
echo "✅ 로봇 완전 정지 완료."

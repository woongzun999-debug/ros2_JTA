#!/bin/bash
# 로봇 즉각 강제 정지 스크립트 (CycloneDDS Domain 30 + /cmd_vel 0.0 + Nav2/순찰 취소 + 토크 OFF)
echo "🛑 로봇 강제 정지 명령 전송 중..."

STOP_CMD='source /opt/ros/humble/setup.bash &&
export ROS_DOMAIN_ID=30 &&
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp &&
export CYCLONEDDS_URI="file://${HOME}/cyclonedds.xml" &&
python3 -c '\''
import time
import rclpy
from geometry_msgs.msg import Twist
from std_srvs.srv import SetBool
from action_msgs.srv import CancelGoal

rclpy.init()
node = rclpy.create_node("emergency_killer")
pub = node.create_publisher(Twist, "/cmd_vel", 10)
cli_motor = node.create_client(SetBool, "/motor_power")
cli_patrol = node.create_client(SetBool, "/patrol/set_enabled")
cli_cancel = node.create_client(CancelGoal, "/navigate_to_pose/_action/cancel_goal")

# CycloneDDS 그래프 매칭 대기 (초기 패킷 유실 방지)
end_wait = time.monotonic() + 0.35
while time.monotonic() < end_wait:
    rclpy.spin_once(node, timeout_sec=0.05)

stop = Twist()
for _ in range(5):
    pub.publish(stop)
    time.sleep(0.04)

# 1. 모터 토크 즉시 차단
if cli_motor.wait_for_service(timeout_sec=0.8):
    req = SetBool.Request()
    req.data = False
    fut = cli_motor.call_async(req)
    rclpy.spin_until_future_complete(node, fut, timeout_sec=1.5)

# 2. 순찰 및 Nav2 활성 Goal 취소 (재기동 시 급발진 차단)
if cli_patrol.wait_for_service(timeout_sec=0.3):
    req = SetBool.Request()
    req.data = False
    fut = cli_patrol.call_async(req)
    rclpy.spin_until_future_complete(node, fut, timeout_sec=0.8)

if cli_cancel.wait_for_service(timeout_sec=0.3):
    fut = cli_cancel.call_async(CancelGoal.Request())
    rclpy.spin_until_future_complete(node, fut, timeout_sec=0.8)

# 3. 최종 속도 버퍼 0.0 소거
for _ in range(5):
    pub.publish(stop)
    time.sleep(0.04)

node.destroy_node()
if rclpy.ok():
    rclpy.shutdown()
'\'''

if [ -f /run/.containerenv ] || [ -f /.dockerenv ]; then
    bash -c "$STOP_CMD"
else
    distrobox enter humble -- bash -c "$STOP_CMD"
fi
echo "✅ 로봇 완전 정지 완료."

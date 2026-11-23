#!/bin/bash
source /opt/ros/humble/setup.bash
if [ -f "$HOME/ros2_ws/install/setup.bash" ]; then
    source "$HOME/ros2_ws/install/setup.bash"
fi
export ROS_DOMAIN_ID=30
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI="file://$HOME/cyclonedds.xml"
export TURTLEBOT3_MODEL=waffle
export LDS_MODEL=LDS-01

# 🛡️ [Fail-Safe 안전 가드레일] 브링업 완료 후 3초 뒤 모터 잔류 속도 강제 초기화(0.0) 및 토크 차단
(
    sleep 5
    # 1. 속도 0.0 명령 5회 연속 전송하여 OpenCR 속도 버퍼 완전 소거
    for i in {1..5}; do
        ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist '{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}' > /dev/null 2>&1
        sleep 0.1
    done
    # 2. 모터 토크 OFF (명시적 주행 명령 전까지 물리적 바퀴 회전 원천 차단)
    ros2 service call /motor_power std_srvs/srv/SetBool '{data: false}' > /dev/null 2>&1
    echo "[Fail-Safe] Initial motor torque safely DISABLED and cmd_vel cleared."
) &

exec ros2 launch jta_bringup robot.launch.py

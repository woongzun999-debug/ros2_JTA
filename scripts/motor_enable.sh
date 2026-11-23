#!/usr/bin/env bash
# ==============================================================================
# 🚀 Control Laptop: 터틀봇 모터 토크 활성화 (ON) 스크립트
# ==============================================================================
echo "⚙️  로봇 모터 토크(Torque)를 활성화합니다..."
docker exec ros2_humble_basic bash -c \
  "source /opt/ros/humble/setup.bash && export ROS_DOMAIN_ID=30 && export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp && export CYCLONEDDS_URI=file:///root/ros2_JTA/config/cyclonedds.xml && ros2 service call /motor_power std_srvs/srv/SetBool '{data: true}'"

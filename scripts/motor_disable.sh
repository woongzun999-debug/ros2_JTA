#!/usr/bin/env bash
# ==============================================================================
# 🛑 Control Laptop: 터틀봇 모터 토크 비활성화 (OFF / Free-wheel) 스크립트
# ==============================================================================
echo "🛑 로봇 모터 토크(Torque)를 차단합니다 (수동 이동 가능 상태)..."
docker exec ros2_humble_basic bash -c \
  "source /opt/ros/humble/setup.bash && export ROS_DOMAIN_ID=30 && export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp && export CYCLONEDDS_URI=file:///root/ros2_JTA/config/cyclonedds.xml && ros2 service call /motor_power std_srvs/srv/SetBool '{data: false}'"

#!/usr/bin/env bash
# ==============================================================================
# 🎮 Control Laptop: 키보드 원격 조종 (Teleop) 스크립트
# ------------------------------------------------------------------------------
# • SLAM 지도 작성 중 로봇을 돌아다니게 조종하기 위한 키보드 노드
# • 주행 시작 전 자동으로 모터 토크(/motor_power: true)를 활성화
# ==============================================================================

set -e

CONTAINER_NAME="ros2_humble_basic"

echo "⚡ [Teleop] 모터 주행 락을 해제(Torque ON)합니다..."
docker exec "${CONTAINER_NAME}" bash -c \
  "source /opt/ros/humble/setup.bash && export ROS_DOMAIN_ID=30 && export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp && export CYCLONEDDS_URI=file:///root/ros2_JTA/config/cyclonedds.xml && ros2 service call /motor_power std_srvs/srv/SetBool '{data: true}'" > /dev/null 2>&1 || true

echo "=========================================================="
echo " 🕹️  터틀봇 키보드 원격 조종 (TurtleBot3 Teleop)"
echo " - w/x: 전진/후진 가속"
echo " - a/d: 좌회전/우회전 가속"
echo " - s / Space: 긴급 정지"
echo "=========================================================="

docker exec -it \
  "${CONTAINER_NAME}" \
  bash -c "source /opt/ros/humble/setup.bash && \
           export ROS_DOMAIN_ID=30 && \
           export TURTLEBOT3_MODEL=waffle && \
           export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp && \
           export CYCLONEDDS_URI=file:///root/ros2_JTA/config/cyclonedds.xml && \
           ros2 run turtlebot3_teleop teleop_keyboard"

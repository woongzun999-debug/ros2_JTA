#!/usr/bin/env bash
# ==============================================================================
# 🚀 [Tier 1 Raspberry Pi 5] 로컬 Nav2 자율주행 실행기
# ------------------------------------------------------------------------------
# • 연산 위치: Raspberry Pi 5 (<PI5_IP>) Distrobox humble
# • 맵 파일: ~/maps/my_map.yaml
# ==============================================================================

set -e

PI_IP="${JTA_PI5_IP:-<PI5_IP>}"
PI_USER="${JTA_PI5_USER:-<PI_USER>}"
MAP_FILE="${1:-~/maps/my_map_261113.yaml}"

echo "=========================================================="
echo " 🤖 [Pi 5 로컬] Nav2 자율주행 기동"
echo " - 기기: Raspberry Pi 5 (${PI_IP})"
echo " - 적용 지도: ${MAP_FILE}"
echo "=========================================================="

# 1. 모터 토크 ON 보장
docker exec ros2_humble_basic bash -c \
  "source /opt/ros/humble/setup.bash && export ROS_DOMAIN_ID=30 && export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp && export CYCLONEDDS_URI=file:///root/ros2_JTA/config/cyclonedds.xml && ros2 service call /motor_power std_srvs/srv/SetBool '{data: true}'" > /dev/null 2>&1 || true

# 2. Pi 5 로컬 Nav2 런칭 (jta_bringup/nav2.launch.py 호출)
ssh -t "${PI_USER}@${PI_IP}" "distrobox enter humble -- bash -ic 'source /opt/ros/humble/setup.bash && source ~/ros2_ws/install/setup.bash && export TURTLEBOT3_MODEL=waffle && export ROS_DOMAIN_ID=30 && export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp && export CYCLONEDDS_URI=file://\$HOME/cyclonedds.xml && ros2 launch jta_bringup nav2.launch.py map:=${MAP_FILE}'"

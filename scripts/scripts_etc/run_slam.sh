#!/usr/bin/env bash
# ==============================================================================
# 🗺️ Control Laptop: SLAM (지도 작성) 원클릭 실행 스크립트
# ------------------------------------------------------------------------------
# • Cartographer 또는 SLAM Toolbox를 노트북 GPU 컨테이너(ros2_humble_basic)에서 연산
# • Pi 5의 센서 데이터(/scan, /odom, /tf)를 받아 실시간 2D Occupancy Grid(/map) 작성
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINER_NAME="ros2_humble_basic"
SLAM_METHOD="${1:-cartographer}"  # 기본값: cartographer (또는 slam_toolbox)

xhost +local:root > /dev/null 2>&1 || true

echo "=========================================================="
echo " 🗺️  JTA Bot 2D SLAM (지도 작성) 기동"
echo " - 연산 노드: Control Laptop (Tier 3 Docker)"
echo " - 센서 입력: Pi 5 (/scan, /tf, /odom)"
echo " - SLAM 방식: ${SLAM_METHOD}"
echo "=========================================================="

if [ "$SLAM_METHOD" == "slam_toolbox" ]; then
    docker exec -it \
      -e DISPLAY="${DISPLAY:-:0}" \
      -e QT_X11_NO_MITSHM=1 \
      "${CONTAINER_NAME}" \
      bash -c "source /opt/ros/humble/setup.bash && \
               export ROS_DOMAIN_ID=30 && \
               export TURTLEBOT3_MODEL=waffle && \
               export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp && \
               export CYCLONEDDS_URI=file:///root/ros2_JTA/config/cyclonedds.xml && \
               ros2 launch slam_toolbox online_async_launch.py"
else
    docker exec -it \
      -e DISPLAY="${DISPLAY:-:0}" \
      -e QT_X11_NO_MITSHM=1 \
      "${CONTAINER_NAME}" \
      bash -c "source /opt/ros/humble/setup.bash && \
               export ROS_DOMAIN_ID=30 && \
               export TURTLEBOT3_MODEL=waffle && \
               export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp && \
               export CYCLONEDDS_URI=file:///root/ros2_JTA/config/cyclonedds.xml && \
               ros2 launch turtlebot3_cartographer cartographer.launch.py"
fi

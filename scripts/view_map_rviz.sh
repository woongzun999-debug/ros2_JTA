#!/usr/bin/env bash
# ==============================================================================
# 🗺️ Control Laptop: 로봇 없이 저장된 지도를 RViz2로 즉시 열람하는 뷰어 스크립트
# ==============================================================================
set -e

CONTAINER_NAME="ros2_humble_basic"
xhost +local:root > /dev/null 2>&1 || true

docker exec -it \
  -e DISPLAY="${DISPLAY:-:0}" \
  -e QT_X11_NO_MITSHM=1 \
  "${CONTAINER_NAME}" \
  bash -c "source /opt/ros/humble/setup.bash && python3 /root/ros2_JTA/scripts/map_rviz_launcher.py"

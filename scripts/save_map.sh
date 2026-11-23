#!/usr/bin/env bash
# ==============================================================================
# 💾 Control Laptop: 완성된 2D 지도 파일 저장 스크립트
# ------------------------------------------------------------------------------
# • maps/my_map.yaml 및 maps/my_map.pgm 으로 저장
# ==============================================================================

set -e

MAP_NAME="${1:-my_map}"
CONTAINER_NAME="ros2_humble_basic"
TARGET_DIR="/root/ros2_JTA/maps"

echo "💾 현재 /map 데이터를 '${TARGET_DIR}/${MAP_NAME}' 으로 저장합니다..."

docker exec -it \
  "${CONTAINER_NAME}" \
  bash -c "source /opt/ros/humble/setup.bash && \
           export ROS_DOMAIN_ID=30 && \
           export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp && \
           export CYCLONEDDS_URI=file:///root/ros2_JTA/config/cyclonedds.xml && \
           ros2 run nav2_map_server map_saver_cli -f ${TARGET_DIR}/${MAP_NAME}"

echo "✅ 지도 저장 완료! (위치: maps/${MAP_NAME}.yaml, maps/${MAP_NAME}.pgm)"

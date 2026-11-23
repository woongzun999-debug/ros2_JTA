#!/usr/bin/env bash
# ==============================================================================
# 🚀 Control Laptop: Nav2 자율주행 엔진 및 RViz2 관제 실행기 (동기화 블로킹 해제)
# ==============================================================================

set -e

MAP_FILE="/root/ros2_JTA/maps/my_map.yaml"
CONTAINER_NAME="ros2_humble_basic"

xhost +local:root > /dev/null 2>&1 || true

echo "=========================================================="
echo " 🤖 [Control Laptop] Nav2 자율주행 & 3D 관제 가동"
echo " - 적용 지도: ${MAP_FILE}"
echo " - 센서/모터 연동: Pi 5 (${JTA_PI5_IP:-<PI5_IP>})"
echo "=========================================================="

# 1. 잔류 프로세스 선제적 안전 정리
docker exec "${CONTAINER_NAME}" pkill -9 -f "nav2|amcl|map_server|planner|controller|service call" > /dev/null 2>&1 || true

# 2. 백그라운드로 모터 토크 ON (타임아웃 2초로 스크립트 멈춤 원천 방지)
docker exec -d "${CONTAINER_NAME}" bash -c \
  "source /opt/ros/humble/setup.bash && export ROS_DOMAIN_ID=30 && timeout 2 ros2 service call /motor_power std_srvs/srv/SetBool '{data: true}' > /dev/null 2>&1" || true

# 3. 노트북 도커에서 Nav2 풀 스택 + RViz2 즉시 실행
docker exec -it \
  -e DISPLAY="${DISPLAY:-:0}" \
  -e QT_X11_NO_MITSHM=1 \
  "${CONTAINER_NAME}" \
  bash -c "source /opt/ros/humble/setup.bash && \
           export TURTLEBOT3_MODEL=waffle && \
           export ROS_DOMAIN_ID=30 && \
           ros2 launch nav2_bringup bringup_launch.py \
             map:=${MAP_FILE} \
             params_file:=/opt/ros/humble/share/turtlebot3_navigation2/param/humble/waffle.yaml \
             use_sim_time:=False \
             use_composition:=False & \
           sleep 2 && \
           rviz2 -d /opt/ros/humble/share/nav2_bringup/rviz/nav2_default_view.rviz"

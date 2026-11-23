#!/usr/bin/env bash
# ==============================================================================
# 🎯 Control Laptop: Nav2 전용 RViz2 뷰어 실행기
# ==============================================================================
set -e
CONTAINER_NAME="ros2_humble_basic"
xhost +local:root > /dev/null 2>&1 || true

docker exec -it \
  -e DISPLAY="${DISPLAY:-:0}" \
  -e QT_X11_NO_MITSHM=1 \
  "${CONTAINER_NAME}" \
  bash -c "source /opt/ros/humble/setup.bash && \
           export ROS_DOMAIN_ID=30 && \
           export TURTLEBOT3_MODEL=waffle && \
           export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp && \
           export CYCLONEDDS_URI=file:///root/ros2_JTA/config/cyclonedds.xml && \
           rviz2 -d /opt/ros/humble/share/nav2_bringup/rviz/nav2_default_view.rviz"

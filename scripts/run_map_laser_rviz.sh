#!/usr/bin/env bash
set -e

CONTAINER_NAME="ros2_humble_basic"
xhost +local:root > /dev/null 2>&1 || true

# 1. 이전 잔류 정리
docker exec "${CONTAINER_NAME}" pkill -9 -f "map_server|amcl|rviz2|static_transform_publisher" > /dev/null 2>&1 || true

# 2. Map Server 기동 & 즉시 활성화
docker exec -d "${CONTAINER_NAME}" bash -c "source /opt/ros/humble/setup.bash && export ROS_DOMAIN_ID=30 && ros2 run nav2_map_server map_server --ros-args -p yaml_filename:=/root/ros2_JTA/maps/my_map_261113.yaml -p use_sim_time:=False"
sleep 1.5
docker exec "${CONTAINER_NAME}" bash -c "source /opt/ros/humble/setup.bash && export ROS_DOMAIN_ID=30 && ros2 lifecycle set /map_server configure && ros2 lifecycle set /map_server activate" > /dev/null 2>&1 || true

# 3. 맵 좌표계(map)와 라이다/오도메트리(odom)를 직접 1:1 결속하는 고속 TF 브릿지 기동 (AMCL 수렴 대기 없이 라이다 즉시 렌더링!)
docker exec -d "${CONTAINER_NAME}" bash -c "source /opt/ros/humble/setup.bash && export ROS_DOMAIN_ID=30 && ros2 run tf2_ros static_transform_publisher 0 0 0 0 0 0 map odom"

# 4. LaserScan이 큼직하고 선명한 빨간점으로 나오는 RViz2 즉시 실행
docker exec -d \
  -e DISPLAY="${DISPLAY:-:0}" \
  -e QT_X11_NO_MITSHM=1 \
  "${CONTAINER_NAME}" \
  bash -c "source /opt/ros/humble/setup.bash && export ROS_DOMAIN_ID=30 && rviz2 -d /root/ros2_JTA/config/rviz2.conf.rviz"

echo "✅ Map + Realtime LiDAR TF Bridge + RViz2 Started!"

#!/bin/bash
# 젯슨 Orin Nano - YOLO11n + VLM + 바닥장애물 + 제스처 통합 ROS 2 Launch 스크립트
source /opt/ros/humble/install/setup.bash
source /root/ros2_ws/install/setup.bash

export ROS_DOMAIN_ID=30
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
if [ -f /root/ros2_ws/cyclonedds.xml ]; then
    export CYCLONEDDS_URI=file:///root/ros2_ws/cyclonedds.xml
elif [ -f "$HOME/cyclonedds.xml" ]; then
    export CYCLONEDDS_URI="file://$HOME/cyclonedds.xml"
fi

# 1. Qwen2-VL 4-bit VLM 상주 노드 (VRAM 1.42GB)
if [ -f /root/ros2_ws/vlm_service_node.py ] && ! pgrep -f "/root/ros2_ws/vlm_service_node.py" >/dev/null 2>&1; then
    nohup python3 /root/ros2_ws/vlm_service_node.py > /root/ros2_ws/vlm_service.log 2>&1 &
fi

# 2. 전 구간 상시 바닥 장애물 감지 노드 (/vision/scan, /vision/floor_obstacles)
if [ -f /root/ros2_ws/floor_obstacle_node.py ] && ! pgrep -f "/root/ros2_ws/floor_obstacle_node.py" >/dev/null 2>&1; then
    nohup python3 /root/ros2_ws/floor_obstacle_node.py > /root/ros2_ws/floor_obstacle.log 2>&1 &
fi

# 3. ArUco 숫자 카드 + 한 손 Nav2/순찰 제어 노드 (/gesture/cmd, /gesture/state)
if [ -f /root/ros2_ws/gesture_node.py ] && ! pgrep -f "/root/ros2_ws/gesture_node.py" >/dev/null 2>&1; then
    nohup python3 /root/ros2_ws/gesture_node.py > /root/ros2_ws/gesture_node.log 2>&1 &
fi

# 4. YOLO11n 메인 런치 실행 (실시간 JSON 추론)
ros2 launch yolo11n yolo11n.launch.py "$@"

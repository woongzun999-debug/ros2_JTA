#!/bin/bash
# [Jetson Orin Nano] 부팅 시 Docker & YOLO11n ROS 2 자동 시작 스크립트

# 시스템 부팅 및 Docker 데몬 안정화 대기
sleep 10

# 1. ros2_humble_dev 컨테이너 구동 확인
if [ "$(docker inspect -f '{{.State.Running}}' ros2_humble_dev 2>/dev/null)" != "true" ]; then
    docker start ros2_humble_dev
    sleep 3
fi

# 2. 컨테이너 내부에서 launch_yolo11n.sh 백그라운드 구동 (중복 실행 방지)
RUNNING=$(docker exec ros2_humble_dev pgrep -f "yolo_detector" 2>/dev/null)
if [ -z "$RUNNING" ]; then
    docker exec -d ros2_humble_dev bash -c '/root/ros2_ws/launch_yolo11n.sh > /root/ros2_ws/yolo11n_autostart.log 2>&1'
fi

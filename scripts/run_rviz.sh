#!/usr/bin/env bash
# ==============================================================================
# 🎮 Control Laptop: RViz2 원클릭 실행 스크립트 (run_rviz.sh)
# ------------------------------------------------------------------------------
# • 기본 모드: 프로젝트 통합 센서/TF 관제 프리셋 (config/rviz2.conf.rviz)
# • Nav2 모드: ./scripts/run_rviz.sh nav2 ➔ Nav2 전용 패널 & 코스트맵 프리셋
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
MODE="${1:-default}"

if [ "$MODE" == "nav2" ] || [ "$MODE" == "nav" ]; then
    RVIZ_CONFIG="/root/ros2_JTA/config/nav2_default_view.rviz"
    TITLE="🎯 [ros2_JTA] Nav2 자율주행 3D 관제 RViz2"
else
    RVIZ_CONFIG="/root/ros2_JTA/config/rviz2.conf.rviz"
    TITLE="🎯 [ros2_JTA] 3D 센서·디지털 트윈 관제 RViz2"
fi

CONTAINER_NAME="ros2_humble_basic"

# 1. 호스트 X11 디스플레이 허용
xhost +local:root > /dev/null 2>&1 || true

echo "========================================================"
echo " ${TITLE}"
echo " - 로드 프리셋: ${RVIZ_CONFIG}"
echo "========================================================"

# 2. 컨테이너 구동 여부 확인
if [ "$(docker ps -q -f name=^/${CONTAINER_NAME}$)" ]; then
    echo "✅ 이미 실행 중인 Docker 컨테이너 발견: [${CONTAINER_NAME}]"
    echo "🚀 RViz2 관제창을 화면에 띄웁니다..."
    docker exec -it \
      -e DISPLAY="${DISPLAY:-:0}" \
      -e QT_X11_NO_MITSHM=1 \
      "${CONTAINER_NAME}" \
      bash -c "source /opt/ros/humble/setup.bash && \
               export ROS_DOMAIN_ID=30 && \
               export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp && \
               export CYCLONEDDS_URI=file:///root/ros2_JTA/config/cyclonedds.xml && \
               rviz2 -d ${RVIZ_CONFIG}"
else
    echo "⚠️  컨테이너 [${CONTAINER_NAME}] 가 꺼져 있습니다."
    echo "🚀 ros2basic.sh 환경을 통해 관제 환경 및 RViz2를 기동합니다..."
    ROS2_DOCKER_SCRIPT="${ROS2_DOCKER_SCRIPT:-$HOME/ros2_humble_docker/ros2basic.sh}"
    if [ -f "${ROS2_DOCKER_SCRIPT}" ]; then
        "${ROS2_DOCKER_SCRIPT}"
    else
        echo "❌ ${ROS2_DOCKER_SCRIPT} 경로를 찾을 수 없습니다."
        exit 1
    fi
fi

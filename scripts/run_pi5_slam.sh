#!/usr/bin/env bash
# ==============================================================================
# 🗺️ [Tier 1 Raspberry Pi 5] 로컬 SLAM (지도 작성) 원격 실행기
# ------------------------------------------------------------------------------
# • 연산 위치: Raspberry Pi 5 (<PI5_IP>) Distrobox humble
# • 관제 위치: Control Laptop RViz2에서 지도(/map) 실시간 모니터링
# ==============================================================================

set -e

PI_IP="${JTA_PI5_IP:-<PI5_IP>}"
PI_USER="${JTA_PI5_USER:-<PI_USER>}"
SLAM_TYPE="${1:-toolbox}" # 기본값: toolbox (또는 cartographer)

echo "=========================================================="
echo " 🗺️  [Pi 5 로컬] 2D SLAM 지도 작성 기동"
echo " - 연산 노드: Raspberry Pi 5 (${PI_IP}) 로컬"
echo " - SLAM 모드: ${SLAM_TYPE}"
echo "=========================================================="

if [ "$SLAM_TYPE" == "cartographer" ]; then
    ssh -t "${PI_USER}@${PI_IP}" "distrobox enter humble -- bash -ic 'source /opt/ros/humble/setup.bash && export TURTLEBOT3_MODEL=waffle && export ROS_DOMAIN_ID=30 && export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp && export CYCLONEDDS_URI=file://\$HOME/cyclonedds.xml && ros2 launch turtlebot3_cartographer cartographer.launch.py use_sim_time:=False'"
else
    ssh -t "${PI_USER}@${PI_IP}" "distrobox enter humble -- bash -ic 'source /opt/ros/humble/setup.bash && export TURTLEBOT3_MODEL=waffle && export ROS_DOMAIN_ID=30 && export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp && export CYCLONEDDS_URI=file://\$HOME/cyclonedds.xml && ros2 launch slam_toolbox online_async_launch.py use_sim_time:=False'"
fi

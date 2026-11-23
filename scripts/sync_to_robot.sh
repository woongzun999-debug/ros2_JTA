#!/bin/bash
# ==============================================================================
# ros2_JTA 3-Tier Code Synchronization Harness (scripts/sync_to_robot.sh)
# ------------------------------------------------------------------------------
# 로컬 레포지토리의 수정 코드를 사전 검증 후 RPi5 및 Jetson으로 안전하게 배포합니다.
# 사용법:
#   bash scripts/sync_to_robot.sh [--dry-run] [pi5|jetson|all]
# ==============================================================================

set -euo pipefail

GREEN="\033[92m"
YELLOW="\033[93m"
RED="\033[91m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DRY_RUN=false
TARGET="all"

for arg in "$@"; do
    case "$arg" in
        --dry-run|-n)
            DRY_RUN=true
            ;;
        pi5|jetson|all)
            TARGET="$arg"
            ;;
        *)
            echo -e "${RED}사용법: bash scripts/sync_to_robot.sh [--dry-run] [pi5|jetson|all]${RESET}"
            exit 1
            ;;
    esac
done

echo -e "${BOLD}=================================================================${RESET}"
echo -e "${BOLD} 🚀 ros2_JTA 3-Tier 코드 동기화 하네스 (Target: ${TARGET}, Dry-Run: ${DRY_RUN})${RESET}"
echo -e "${BOLD}=================================================================${RESET}"

# 1. 사전 정적 검증 하네스 실행
echo -e "\n${CYAN}>>> [Step 1] 로컬 코드베이스 안전성 검증 실행 중...${RESET}"
if ! python3 "${REPO_ROOT}/scripts/validate_ros2_harness.py"; then
    echo -e "${RED}[중단] 로컬 하네스 검증에 실패하여 원격 배포를 차단했습니다.${RESET}"
    exit 1
fi

RSYNC_FLAGS="-avz --progress"
if [[ "$DRY_RUN" == true ]]; then
    RSYNC_FLAGS="-avzn --progress"
    echo -e "\n${YELLOW}>>> [DRY-RUN 모드] 실제 파일은 전송되지 않고 변경 예정 목록만 표시됩니다.${RESET}"
fi

# 2. Tier 1: Raspberry Pi 5 동기화
sync_pi5() {
    local rpi_ip="${JTA_PI5_IP:-<PI5_IP>}"
    local rpi_user="${JTA_PI5_USER:-<PI_USER>}"
    local rpi_host="${rpi_user}@${rpi_ip}"
    echo -e "\n${BOLD}${CYAN}>>> [Tier 1] Raspberry Pi 5 (${rpi_host}) 동기화 시작...${RESET}"
    if ! ping -c 1 -W 1 "${rpi_ip}" >/dev/null 2>&1; then
        echo -e "  ${YELLOW}[SKIP]${RESET} ${rpi_ip} 기기가 현재 오프라인입니다."
        return 0
    fi
    rsync ${RSYNC_FLAGS} \
        "${REPO_ROOT}/src/pi5/camera_ros2_stream_optimized.py" \
        "${REPO_ROOT}/src/pi5/obstacle_avoidance.py" \
        "${REPO_ROOT}/src/pi5/utils/check_power.py" \
        "${REPO_ROOT}/src/pi5/utils/stop.sh" \
        "${REPO_ROOT}/config/cyclonedds.xml" \
        "${rpi_host}:~/"

    # jta_bringup speaker_node.py, nav2_params.yaml, nav2.launch.py, blank_filter_mask.* 동기화 (src 및 install 동시 반영)
    rsync ${RSYNC_FLAGS} \
        "${REPO_ROOT}/src/pi5/jta_bringup/jta_bringup/speaker_node.py" \
        "${REPO_ROOT}/src/pi5/jta_bringup/jta_bringup/patrol_node.py" \
        "${rpi_host}:~/ros2_ws/src/jta_bringup/jta_bringup/"
    rsync ${RSYNC_FLAGS} \
        "${REPO_ROOT}/src/pi5/jta_bringup/jta_bringup/speaker_node.py" \
        "${REPO_ROOT}/src/pi5/jta_bringup/jta_bringup/patrol_node.py" \
        "${rpi_host}:~/ros2_ws/install/jta_bringup/lib/python3.10/site-packages/jta_bringup/"
    rsync ${RSYNC_FLAGS} \
        "${REPO_ROOT}/src/pi5/jta_bringup/config/nav2_params.yaml" \
        "${REPO_ROOT}/src/pi5/jta_bringup/config/blank_filter_mask.yaml" \
        "${REPO_ROOT}/src/pi5/jta_bringup/config/blank_filter_mask.pgm" \
        "${rpi_host}:~/ros2_ws/src/jta_bringup/config/"
    rsync ${RSYNC_FLAGS} \
        "${REPO_ROOT}/src/pi5/jta_bringup/config/nav2_params.yaml" \
        "${REPO_ROOT}/src/pi5/jta_bringup/config/blank_filter_mask.yaml" \
        "${REPO_ROOT}/src/pi5/jta_bringup/config/blank_filter_mask.pgm" \
        "${rpi_host}:~/ros2_ws/install/jta_bringup/share/jta_bringup/config/"
    rsync ${RSYNC_FLAGS} \
        "${REPO_ROOT}/src/pi5/jta_bringup/launch/nav2.launch.py" \
        "${rpi_host}:~/ros2_ws/src/jta_bringup/launch/nav2.launch.py"
    rsync ${RSYNC_FLAGS} \
        "${REPO_ROOT}/src/pi5/jta_bringup/launch/nav2.launch.py" \
        "${rpi_host}:~/ros2_ws/install/jta_bringup/share/jta_bringup/launch/nav2.launch.py"

    # Pi 5 상시 WebUI (:9999, ~/jta_web/) 서버 및 <canvas> 정적 오버레이 동기화
    rsync ${RSYNC_FLAGS} \
        "${REPO_ROOT}/src/laptop/web/web_monitor_server.py" \
        "${rpi_host}:~/jta_web/"
    rsync ${RSYNC_FLAGS} \
        "${REPO_ROOT}/src/laptop/web/static/index.html" \
        "${REPO_ROOT}/src/laptop/web/static/map.html" \
        "${REPO_ROOT}/src/laptop/web/static/yolo_overlay.js" \
        "${rpi_host}:~/jta_web/static/"

    echo -e "  ${GREEN}[완료]${RESET} Tier 1 (RPi5) 동기화 완료."
}

# 3. Tier 2: Jetson Orin Nano 동기화
sync_jetson() {
    local jetson_ip="${JTA_JETSON_IP:-<JETSON_IP>}"
    local jetson_user="${JTA_JETSON_USER:-<JETSON_USER>}"
    local jetson_host="${jetson_user}@${jetson_ip}"
    local jetson_port="${JTA_JETSON_PORT:-2222}"
    echo -e "\n${BOLD}${CYAN}>>> [Tier 2] Jetson Orin Nano (${jetson_host}:${jetson_port}) 동기화 시작...${RESET}"
    if ! ping -c 1 -W 1 "${jetson_ip}" >/dev/null 2>&1; then
        echo -e "  ${YELLOW}[SKIP]${RESET} ${jetson_ip} 기기가 현재 오프라인입니다."
        return 0
    fi
    rsync ${RSYNC_FLAGS} -e "ssh -p ${jetson_port}" \
        "${REPO_ROOT}/src/jetson/jetson_yolo_node.py" \
        "${REPO_ROOT}/src/jetson/vlm_service_node.py" \
        "${REPO_ROOT}/src/jetson/gesture_node.py" \
        "${REPO_ROOT}/src/jetson/floor_obstacle_node.py" \
        "${REPO_ROOT}/src/jetson/scripts/launch_yolo11n.sh" \
        "${REPO_ROOT}/config/cyclonedds.xml" \
        "${jetson_host}:~/ros2_ws/"
    rsync ${RSYNC_FLAGS} -e "ssh -p ${jetson_port}" \
        "${REPO_ROOT}/src/jetson/yolo11n/yolo11n/detector_node.py" \
        "${jetson_host}:~/ros2_ws/src/yolo11n/yolo11n/detector_node.py"
    rsync ${RSYNC_FLAGS} -e "ssh -p ${jetson_port}" \
        "${REPO_ROOT}/src/jetson/jetson_yolo_node.py" \
        "${REPO_ROOT}/src/jetson/vlm_service_node.py" \
        "${REPO_ROOT}/src/jetson/gesture_node.py" \
        "${REPO_ROOT}/src/jetson/floor_obstacle_node.py" \
        "${jetson_host}:~/ros2_JTA/src/jetson/"
    rsync ${RSYNC_FLAGS} -e "ssh -p ${jetson_port}" \
        "${REPO_ROOT}/src/jetson/scripts/launch_yolo11n.sh" \
        "${jetson_host}:~/ros2_JTA/src/jetson/scripts/"
    rsync ${RSYNC_FLAGS} -e "ssh -p ${jetson_port}" \
        "${REPO_ROOT}/src/jetson/yolo11n/yolo11n/detector_node.py" \
        "${jetson_host}:~/ros2_JTA/src/jetson/yolo11n/yolo11n/detector_node.py"
    echo -e "  ${GREEN}[완료]${RESET} Tier 2 (Jetson) 동기화 완료."
}

case "$TARGET" in
    pi5)
        sync_pi5
        ;;
    jetson)
        sync_jetson
        ;;
    all)
        sync_pi5
        sync_jetson
        ;;
esac

echo -e "\n${BOLD}${GREEN}✅ 하네스 동기화 프로세스가 정상 종료되었습니다.${RESET}"

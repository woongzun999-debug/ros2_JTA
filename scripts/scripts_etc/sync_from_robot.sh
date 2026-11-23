#!/bin/bash
# ==============================================================================
# ros2_JTA 3-Tier Code Reverse-Synchronization Harness (scripts/sync_from_robot.sh)
# ------------------------------------------------------------------------------
# RPi5 또는 Jetson에서 직접 수정한 코드를 로컬 메인 레포지토리로 안전하게 가져옵니다(Pull).
# 가져온 직후 로컬 하네스 검증을 자동으로 실행합니다.
# 사용법:
#   bash scripts/sync_from_robot.sh [pi5|jetson|all]
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

TARGET="${1:-all}"

echo -e "${BOLD}=================================================================${RESET}"
echo -e "${BOLD} 📥 ros2_JTA 3-Tier 로봇 ➔ 로컬 역동기화 하네스 (Target: ${TARGET})${RESET}"
echo -e "${BOLD}=================================================================${RESET}"

pull_pi5() {
    local rpi_ip="${JTA_PI5_IP:-<PI5_IP>}"
    local rpi_user="${JTA_PI5_USER:-<PI_USER>}"
    local rpi_host="${rpi_user}@${rpi_ip}"
    echo -e "\n${BOLD}${CYAN}>>> [Tier 1] RPi5 (${rpi_host}) ➔ 로컬 메인 디렉토리 가져오기...${RESET}"
    if ! ping -c 1 -W 1 "${rpi_ip}" >/dev/null 2>&1; then
        echo -e "  ${YELLOW}[SKIP]${RESET} RPi5(${rpi_ip}) 기기가 오프라인입니다."
        return 0
    fi
    rsync -avz "${rpi_host}:~/camera_ros2_stream_optimized.py" "${REPO_ROOT}/src/pi5/"
    echo -e "  ${GREEN}[완료]${RESET} RPi5 카메라 코드 동기화 완료."
}

pull_jetson() {
    local jetson_ip="${JTA_JETSON_IP:-<JETSON_IP>}"
    local jetson_user="${JTA_JETSON_USER:-<JETSON_USER>}"
    local jetson_host="${jetson_user}@${jetson_ip}"
    local jetson_port="${JTA_JETSON_PORT:-2222}"
    echo -e "\n${BOLD}${CYAN}>>> [Tier 2] Jetson (${jetson_host}:${jetson_port}) ➔ 로컬 메인 디렉토리 가져오기...${RESET}"
    if ! ping -c 1 -W 1 "${jetson_ip}" >/dev/null 2>&1; then
        echo -e "  ${YELLOW}[SKIP]${RESET} Jetson(${jetson_ip}) 기기가 오프라인입니다."
        return 0
    fi
    rsync -avz -e "ssh -p ${jetson_port}" \
        "${jetson_host}:~/ros2_ws/jetson_yolo_node.py" \
        "${REPO_ROOT}/src/jetson/"
    echo -e "  ${GREEN}[완료]${RESET} Jetson YOLO 코드 동기화 완료."
}

case "$TARGET" in
    pi5)
        pull_pi5
        ;;
    jetson)
        pull_jetson
        ;;
    all)
        pull_pi5
        pull_jetson
        ;;
    *)
        echo -e "${RED}사용법: bash scripts/sync_from_robot.sh [pi5|jetson|all]${RESET}"
        exit 1
        ;;
esac

echo -e "\n${CYAN}>>> 로컬 코드 무결성 및 하네스 검증 실행 중...${RESET}"
python3 "${REPO_ROOT}/scripts/validate_ros2_harness.py"

echo -e "\n${BOLD}${GREEN}✅ 로봇 ➔ 로컬 역동기화 및 안전성 검증이 완료되었습니다.${RESET}"

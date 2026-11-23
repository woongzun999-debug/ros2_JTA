#!/bin/bash
# ==============================================================================
# ros2_JTA 3-Tier 양방향 스마트 동기화 하네스 (scripts/smart_sync.sh)
# ------------------------------------------------------------------------------
# 노트북(로컬), Jetson, RPi5 중 어디서 코드를 수정하든,
# 파일의 최종 수정 시간(Modified Timestamp)을 비교하여 항상 "더 최신 버전"으로
# 자동 감지하여 양방향 동기화(Push 또는 Pull)합니다.
#
# 사용법:
#   bash scripts/smart_sync.sh            # 1회 즉시 양방향 검사 및 최신본 동기화
#   bash scripts/smart_sync.sh --watch    # 백그라운드 실시간 감시 모드 (저장 시 자동 동기화)
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

PI5_IP="${JTA_PI5_IP:-<PI5_IP>}"
PI5_USER="${JTA_PI5_USER:-<PI_USER>}"
JETSON_IP="${JTA_JETSON_IP:-<JETSON_IP>}"
JETSON_USER="${JTA_JETSON_USER:-<JETSON_USER>}"
PI5_HOST="${PI5_USER}@${PI5_IP}"
JETSON_HOST="${JETSON_USER}@${JETSON_IP}"
JETSON_PORT="${JTA_JETSON_PORT:-2222}"

sync_pair() {
    local name="$1"
    local local_file="$2"
    local remote_host="$3"
    local remote_port="$4"
    local remote_file="$5"

    if [ ! -f "$local_file" ]; then
        return 0
    fi

    local ssh_cmd="ssh"
    if [ -n "$remote_port" ]; then
        ssh_cmd="ssh -p ${remote_port}"
    fi

    # 1. 파일 수정 타임스탬프(Unix epoch seconds) 획득
    local local_mtime
    local_mtime=$(stat -c %Y "$local_file" 2>/dev/null || echo 0)

    local remote_mtime
    remote_mtime=$($ssh_cmd "$remote_host" "stat -c %Y '$remote_file' 2>/dev/null" || echo 0)

    if [ "$remote_mtime" -eq 0 ]; then
        echo -e "  ${YELLOW}[SKIP]${RESET} 원격 기기 연결 불가 또는 파일 없음: ${name}"
        return 0
    fi

    # 2. 타임스탬프 비교 판별
    local diff=$((local_mtime - remote_mtime))

    if [ "$diff" -gt 1 ]; then
        # 로컬이 더 최신 -> 원격으로 Push
        echo -e "  ${CYAN}[⬆️ 로컬이 더 최신 (${diff}초 차이)]${RESET} 로컬 ➔ ${name} 배포 중..."
        if [ -n "$remote_port" ]; then
            rsync -avz -e "ssh -p ${remote_port}" "$local_file" "${remote_host}:${remote_file}" > /dev/null
        else
            rsync -avz "$local_file" "${remote_host}:${remote_file}" > /dev/null
        fi
        # 타임스탬프 동일 맞춤
        $ssh_cmd "$remote_host" "touch -r '$remote_file' '$remote_file'"
        echo -e "    ${GREEN}✔ ${name} 원격 파일 갱신 완료.${RESET}"
    elif [ "$diff" -lt -1 ]; then
        # 원격이 더 최신 -> 로컬로 Pull
        local rdiff=$((-diff))
        echo -e "  ${YELLOW}[⬇️ 원격이 더 최신 (${rdiff}초 차이)]${RESET} ${name} ➔ 로컬 가져오는 중..."
        if [ -n "$remote_port" ]; then
            rsync -avz -e "ssh -p ${remote_port}" "${remote_host}:${remote_file}" "$local_file" > /dev/null
        else
            rsync -avz "${remote_host}:${remote_file}" "$local_file" > /dev/null
        fi
        echo -e "    ${GREEN}✔ 로컬 파일 갱신 완료.${RESET}"
    else
        echo -e "  ${GREEN}[✅ 최신 상태 동일]${RESET} ${name}: 동기화 유지 중"
    fi
}

run_sync_cycle() {
    echo -e "${BOLD}${CYAN}>>> 양방향 타임스탬프 동기화 검사 시작...${RESET}"

    # 1. Jetson YOLO 코드 동기화
    if ping -c 1 -W 1 "${JETSON_IP}" >/dev/null 2>&1; then
        sync_pair "Jetson YOLO" \
            "${REPO_ROOT}/src/jetson/jetson_yolo_node.py" \
            "${JETSON_HOST}" "${JETSON_PORT}" "~/ros2_ws/jetson_yolo_node.py"
        # Jetson git 저장소도 함께 갱신
        ssh -p "${JETSON_PORT}" "${JETSON_HOST}" "cp ~/ros2_ws/jetson_yolo_node.py ~/ros2_JTA/src/jetson/jetson_yolo_node.py 2>/dev/null || true"
    else
        echo -e "  ${YELLOW}[SKIP]${RESET} Jetson(${JETSON_IP}) 오프라인"
    fi

    # 2. RPi5 카메라 코드 동기화
    if ping -c 1 -W 1 "${PI5_IP}" >/dev/null 2>&1; then
        sync_pair "RPi5 Camera" \
            "${REPO_ROOT}/src/pi5/camera_ros2_stream_optimized.py" \
            "${PI5_HOST}" "" "~/camera_ros2_stream_optimized.py"
    else
        echo -e "  ${YELLOW}[SKIP]${RESET} RPi5(${PI5_IP}) 오프라인"
    fi
}

if [[ "${1:-}" == "--watch" || "${1:-}" == "-w" ]]; then
    echo -e "${BOLD}=================================================================${RESET}"
    echo -e "${BOLD} 🔄 실시간 감시 모드 시작 (3초 간격 자동 양방향 최신본 싱크, 종료: Ctrl+C)${RESET}"
    echo -e "${BOLD}=================================================================${RESET}"
    while true; do
        run_sync_cycle
        sleep 3
    done
else
    echo -e "${BOLD}=================================================================${RESET}"
    echo -e "${BOLD} 🔄 ros2_JTA 스마트 양방향 최신본 동기화 (Smart Sync)${RESET}"
    echo -e "${BOLD}=================================================================${RESET}"
    run_sync_cycle
    echo -e "\n${CYAN}>>> 로컬 하네스 안전성 검증...${RESET}"
    python3 "${REPO_ROOT}/scripts/validate_ros2_harness.py"
    echo -e "${BOLD}${GREEN}✅ 양방향 동기화 및 검증이 완료되었습니다.${RESET}"
fi

#!/bin/bash
# ==============================================================================
# ros2_JTA 3-Tier System Healthcheck Harness (scripts/healthcheck_3tier.sh)
# ------------------------------------------------------------------------------
# Tier 1: Raspberry Pi 5 (<PI5_IP>:22, <PI_USER>)
# Tier 2: Jetson Orin Nano (<JETSON_IP>:2222, <JETSON_USER>)
# Tier 3: Control Laptop (<LAPTOP_IP>, <LAPTOP_USER>)
# ==============================================================================

set -u

GREEN="\033[92m"
YELLOW="\033[93m"
RED="\033[91m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

QUICK_MODE=false
if [[ "${1:-}" == "--quick" ]]; then
    QUICK_MODE=true
fi

RPI_IP="${JTA_PI5_IP:-<PI5_IP>}"
RPI_USER="${JTA_PI5_USER:-<PI_USER>}"
RPI_PORT="22"

JETSON_IP="${JTA_JETSON_IP:-<JETSON_IP>}"
JETSON_USER="${JTA_JETSON_USER:-<JETSON_USER>}"
JETSON_PORT="2222"

LAPTOP_IP="${JTA_LAPTOP_IP:-<LAPTOP_IP>}"

echo -e "${BOLD}=================================================================${RESET}"
echo -e "${BOLD} 🩺 ros2_JTA 3-Tier 분산 로봇 시스템 상태 진단 (Healthcheck)${RESET}"
echo -e "${BOLD}=================================================================${RESET}"

# 1. Tier 3 (Local Laptop) 점검
echo -e "\n${BOLD}${CYAN}[Tier 3] 관제 노트북 (Host: ${LAPTOP_IP})${RESET}"
if ip addr | grep -q "${LAPTOP_IP}"; then
    echo -e "  ${GREEN}[OK]${RESET} 로컬 IP (${LAPTOP_IP}) 활성화 확인"
else
    echo -e "  ${YELLOW}[WARN]${RESET} 현재 네트워크 인터페이스에서 ${LAPTOP_IP}가 감지되지 않습니다."
fi

if command -v docker >/dev/null 2>&1; then
    RUNNING_CONTAINERS=$(docker ps --format "{{.Names}} ({{.Image}})" 2>/dev/null || true)
    if [[ -n "$RUNNING_CONTAINERS" ]]; then
        echo -e "  ${GREEN}[OK]${RESET} 실행 중인 로컬 Docker 컨테이너: ${RUNNING_CONTAINERS}"
    else
        echo -e "  ${YELLOW}[INFO]${RESET} 현재 실행 중인 관제 Docker 컨테이너 없음 (필요 시 ros2basic.sh 실행)"
    fi
fi

# 2. Tier 1 (Raspberry Pi 5) 점검
echo -e "\n${BOLD}${CYAN}[Tier 1] Raspberry Pi 5 & OpenCR (${RPI_USER}@${RPI_IP}:${RPI_PORT})${RESET}"
if ping -c 1 -W 1 "${RPI_IP}" >/dev/null 2>&1; then
    echo -e "  ${GREEN}[OK]${RESET} 네트워크 Ping 응답 정상 (${RPI_IP})"
    if [[ "$QUICK_MODE" == false ]]; then
        if ssh -o BatchMode=yes -o ConnectTimeout=2 -p "${RPI_PORT}" "${RPI_USER}@${RPI_IP}" "echo ok" >/dev/null 2>&1; then
            echo -e "  ${GREEN}[OK]${RESET} SSH 연결 성공"
            RPI_STATUS=$(ssh -o ConnectTimeout=3 -p "${RPI_PORT}" "${RPI_USER}@${RPI_IP}" '
                echo -n "OpenCR(/dev/ttyACM0): "; [ -e /dev/ttyACM0 ] && echo -n "연결됨 | " || echo -n "미연결 | ";
                echo -n "Camera(/dev/video0): "; [ -e /dev/video0 ] && echo -n "연결됨 | " || echo -n "미연결 | ";
                echo -n "Processes: "; pgrep -fa "camera_ros2_stream|turtlebot3|hlds_laser" | tr "\n" " " || echo "없음"
            ' 2>/dev/null)
            echo -e "  ${CYAN}[INFO]${RESET} 하드웨어 상태: ${RPI_STATUS}"
        else
            echo -e "  ${YELLOW}[WARN]${RESET} SSH 자동 접속 실패 (키 인증 또는 SSH 데몬 확인 필요)"
        fi
    fi
else
    echo -e "  ${RED}[OFFLINE]${RESET} Raspberry Pi 5 (${RPI_IP}) 네트워크 응답 없음 (전원/Wi-Fi 확인)"
fi

# 3. Tier 2 (Jetson Orin Nano) 점검
echo -e "\n${BOLD}${CYAN}[Tier 2] Jetson Orin Nano (${JETSON_USER}@${JETSON_IP}:${JETSON_PORT})${RESET}"
if ping -c 1 -W 1 "${JETSON_IP}" >/dev/null 2>&1; then
    echo -e "  ${GREEN}[OK]${RESET} 네트워크 Ping 응답 정상 (${JETSON_IP})"
    if [[ "$QUICK_MODE" == false ]]; then
        if ssh -o BatchMode=yes -o ConnectTimeout=2 -p "${JETSON_PORT}" "${JETSON_USER}@${JETSON_IP}" "echo ok" >/dev/null 2>&1; then
            echo -e "  ${GREEN}[OK]${RESET} SSH (Port ${JETSON_PORT}) 연결 성공"
            JETSON_DOCKER=$(ssh -o ConnectTimeout=3 -p "${JETSON_PORT}" "${JETSON_USER}@${JETSON_IP}" 'docker ps --format "{{.Names}} ({{.Image}})"' 2>/dev/null || true)
            if [[ -n "$JETSON_DOCKER" ]]; then
                echo -e "  ${GREEN}[OK]${RESET} Jetson Docker 컨테이너 실행 중: ${JETSON_DOCKER}"
            else
                echo -e "  ${YELLOW}[WARN]${RESET} Jetson Docker 컨테이너 미실행 (~/jetson_ros2_humble/run.sh 실행 필요)"
            fi
        else
            echo -e "  ${YELLOW}[WARN]${RESET} SSH (Port ${JETSON_PORT}) 자동 접속 실패"
        fi
    fi
else
    echo -e "  ${RED}[OFFLINE]${RESET} Jetson Orin Nano (${JETSON_IP}) 네트워크 응답 없음 (전원/Wi-Fi 확인)"
fi

echo -e "\n${BOLD}=================================================================${RESET}"
echo -e " 💡 코드 정적 검증을 실행하려면: ${CYAN}python3 scripts/validate_ros2_harness.py${RESET}"
echo -e "${BOLD}=================================================================${RESET}"

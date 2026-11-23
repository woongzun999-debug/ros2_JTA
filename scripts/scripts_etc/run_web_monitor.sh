#!/bin/bash
# ==============================================================================
# JTA Bot WebUI 통합 관제 콕핏 서버 (포트 9999) 원클릭 런처
# ==============================================================================
PORT=${1:-9999}
echo "=========================================================="
echo " 🌐 JTA Bot 3-Tier WebUI Cockpit 기동 (포트 :${PORT})"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
echo " 📡 접속 주소 (데스크톱/모바일 공용): http://${JTA_LAPTOP_IP:-<LAPTOP_IP>}:${PORT}"
echo "=========================================================="

python3 "${REPO_ROOT}/src/laptop/web/web_monitor_server.py" "$PORT"

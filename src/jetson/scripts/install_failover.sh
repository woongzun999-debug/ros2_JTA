#!/usr/bin/env bash
set -e

echo "=========================================================="
echo " [Jetson Failover] Installing Network Failover Daemon... "
echo "=========================================================="

if [ "$EUID" -ne 0 ]; then
    echo "❌ 에러: 이 스크립트는 root 권한(sudo)으로 실행해야 합니다."
    echo "실행 방법: sudo $0"
    exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. Python 데몬 복사
echo "1. /usr/local/bin/network-failover.py 설치 중..."
cp -f "$SRC_DIR/network_failover.py" /usr/local/bin/network-failover.py
chmod 755 /usr/local/bin/network-failover.py

# 2. Systemd 서비스 등록
echo "2. /etc/systemd/system/network-failover.service 등록 중..."
cp -f "$SRC_DIR/network-failover.service" /etc/systemd/system/network-failover.service
chmod 644 /etc/systemd/system/network-failover.service

# 3. 서비스 활성화 및 재시작
echo "3. Systemd 데몬 리로드 및 서비스 시작..."
systemctl daemon-reload
systemctl enable network-failover.service
systemctl restart network-failover.service

echo ""
echo "=========================================================="
echo " ✅ 설치 및 시작 완료!"
echo "=========================================================="
systemctl status network-failover.service --no-pager
echo ""
echo "실시간 페일오버 로그 확인 명령어:"
echo "  journalctl -u network-failover -f"
echo "=========================================================="

#!/usr/bin/env bash

echo "=========================================================="
echo "          Jetson Network Failover Status Check            "
echo "=========================================================="

ETH="enP8p1s0"
WLAN="wlP1p1s0"
TARGET_IP="${JTA_JETSON_IP:-<JETSON_IP>}"
GATEWAY_IP="${JTA_GATEWAY_IP:-<GATEWAY_IP>}"

ETH_CARRIER=$(cat /sys/class/net/$ETH/carrier 2>/dev/null || echo "0")
WLAN_STATE=$(cat /sys/class/net/$WLAN/operstate 2>/dev/null || echo "down")

echo "[1] 물리적 링크 상태"
if [ "$ETH_CARRIER" = "1" ]; then
    echo "  - 유선 랜 ($ETH): [연결됨 (Carrier UP)] 🟢"
else
    echo "  - 유선 랜 ($ETH): [단선됨 (No Carrier)] 🔴"
fi
echo "  - 무선 랜 ($WLAN): [$WLAN_STATE] 🔵"

echo ""
echo "[2] IP ($TARGET_IP) 활성 위치"
ETH_HAS_IP=$(ip -4 -brief addr show dev $ETH 2>/dev/null | grep "$TARGET_IP" || true)
WLAN_HAS_IP=$(ip -4 -brief addr show dev $WLAN 2>/dev/null | grep "$TARGET_IP" || true)

if [ -n "$ETH_HAS_IP" ]; then
    echo "  - 현재 $TARGET_IP 보유: [유선 랜 $ETH] (PRIMARY)"
elif [ -n "$WLAN_HAS_IP" ]; then
    echo "  - 현재 $TARGET_IP 보유: [무선 랜 $WLAN] (BACKUP FAILOVER)"
else
    echo "  - 경고: 어느 인터페이스에도 $TARGET_IP 가 없습니다."
fi

echo ""
echo "[3] 기본 게이트웨이 라우트"
ip route show default

echo ""
echo "[4] 데몬 서비스 상태"
if systemctl is-active --quiet network-failover.service 2>/dev/null; then
    echo "  - network-failover.service: [실행 중 (ACTIVE)] 🟢"
else
    echo "  - network-failover.service: [중지됨 또는 미등록] ⚪"
fi

echo ""
echo "[5] 통신 테스트"
echo -n "  - 게이트웨이 ($GATEWAY_IP) 핑: "
if ping -c 1 -W 1 "$GATEWAY_IP" >/dev/null 2>&1; then
    echo "정상 🟢"
else
    echo "실패 🔴"
fi

echo -n "  - 외부 인터넷 (8.8.8.8) 핑: "
if ping -c 1 -W 1 8.8.8.8 >/dev/null 2>&1; then
    echo "정상 🟢"
else
    echo "실패 🔴"
fi

echo "=========================================================="

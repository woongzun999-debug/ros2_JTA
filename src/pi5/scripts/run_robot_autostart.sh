#!/bin/bash
# [Raspberry Pi 5] 부팅 시 Distrobox humble 및 robot.launch.py 자동 실행 스크립트

export USER="${USER:-<PI_USER>}"
export HOME="${HOME:-/home/${USER}}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

sleep 5
# 부팅 세레머니 플래그 초기화
rm -f /dev/shm/pi5_boot_ceremony_done /tmp/pi5_boot_ceremony_done > /dev/null 2>&1

# 1. OpenCR 디바이스 연결 확인 (최대 15초 대기)
COUNT=0
while [ ! -e /dev/ttyACM0 ] && [ $COUNT -lt 15 ]; do
    sleep 1
    COUNT=$((COUNT + 1))
done

# 2. 호스트 백그라운드 전원 감시 데몬 기동 (중복 방지)
if ! pgrep -f "pi5_power_daemon.py" > /dev/null; then
    nohup /usr/bin/python3 "$HOME/pi5_power_daemon.py" > /dev/null 2>&1 &
fi

# 3. 하드웨어 포트 잔류 프로세스 선제적 안전 해제 (Pre-Clean 원칙)
fuser -k /dev/ttyACM0 /dev/ttyUSB0 /dev/video0 > /dev/null 2>&1
sleep 1

# 4. 프로세스 중복 실행 확인
RUNNING=$(pgrep -f "robot.launch.py|turtlebot3_ros|hlds_laser_publisher" 2>/dev/null)
if [ -n "$RUNNING" ]; then
    echo "[autostart] Robot node is already running." >> "$HOME/robot_autostart.log"
    exit 0
fi

# 5. Distrobox humble 내부에서 실행 스크립트 호출
nohup distrobox enter humble -- "$HOME/inner_robot_launch.sh" > "$HOME/robot_autostart.log" 2>&1 &

echo "[autostart] Successfully initiated robot.launch.py at $(date)" >> "$HOME/robot_autostart.log"

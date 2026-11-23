#!/usr/bin/env python3
"""
라즈베리파이 5 전원 및 쓰로틀링 상태 + OpenCR 배터리 진단 도구 (check_power)
- 라즈베리파이 5: 입력 전압(5V), 코어 전압/전류, CPU 온도, 클럭, 저전압/쓰로틀링 이력
- OpenCR / TurtleBot3: Li-Po 배터리 실시간 전압, 잔여량(%), 셀당 전압, 상태 분석
- 단일 실행 및 실시간 모니터링(-w / --watch) 지원
"""
import sys
import os
import time
import subprocess
import argparse

# ANSI 색상 코드
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def run_cmd(cmd):
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def get_pmic_data():
    out = run_cmd(['vcgencmd', 'pmic_read_adc'])
    data = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        key = parts[0]
        val_part = parts[-1]
        if '=' in val_part:
            raw_val = val_part.split('=')[1]
            try:
                unit = "V" if raw_val.endswith("V") else ("A" if raw_val.endswith("A") else "")
                num = float(raw_val.rstrip("VA"))
                data[key] = f"{num:.2f} {unit}".strip()
            except Exception:
                data[key] = raw_val
    return data


def decode_throttled(val_int):
    return {
        'under_voltage_now': bool(val_int & (1 << 0)),
        'freq_capped_now': bool(val_int & (1 << 1)),
        'throttled_now': bool(val_int & (1 << 2)),
        'temp_limit_now': bool(val_int & (1 << 3)),
        'under_voltage_past': bool(val_int & (1 << 16)),
        'freq_capped_past': bool(val_int & (1 << 17)),
        'throttled_past': bool(val_int & (1 << 18)),
        'temp_limit_past': bool(val_int & (1 << 19)),
    }


def get_opencr_battery():
    """OpenCR 터틀봇3 배터리 정보 조회"""
    if not os.path.exists('/dev/ttyACM0'):
        return {'status': 'disconnected', 'msg': 'OpenCR USB 미연결 (/dev/ttyACM0 없음)'}

    tb_running = False
    try:
        out = subprocess.check_output(['pgrep', '-f', 'turtlebot3_ros'], text=True)
        if out.strip():
            tb_running = True
    except Exception:
        pass

    if not tb_running:
        return {
            'status': 'stopped',
            'msg': 'OpenCR 노드 미실행 (bringup 필요: ros2 launch turtlebot3_bringup robot.launch.py)'
        }

    try:
        cmd = [
            'distrobox', 'enter', 'humble', '--',
            'bash', '-c',
            'source /opt/ros/humble/setup.bash && ros2 topic echo /battery_state --once'
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.5)
        if res.returncode == 0 and 'voltage:' in res.stdout:
            voltage = None
            pct = None
            for line in res.stdout.splitlines():
                if 'voltage:' in line:
                    voltage = float(line.split(':')[1].strip())
                elif 'percentage:' in line:
                    pct = float(line.split(':')[1].strip())
            return {
                'status': 'ok',
                'voltage': voltage,
                'percentage': pct
            }
        else:
            return {'status': 'waiting', 'msg': '배터리 토픽 수신 대기 중...'}
    except subprocess.TimeoutExpired:
        return {'status': 'timeout', 'msg': '토픽 응답 시간 초과'}
    except Exception as e:
        return {'status': 'error', 'msg': str(e)}


def print_status(clear_screen=False):
    if clear_screen:
        print("\033[H\033[J", end="")

    throttled_str = run_cmd(['vcgencmd', 'get_throttled'])
    throttled_hex = 0
    if '=' in throttled_str:
        try:
            throttled_hex = int(throttled_str.split('=')[1], 16)
        except ValueError:
            pass

    flags = decode_throttled(throttled_hex)
    pmic = get_pmic_data()
    temp_str = run_cmd(['vcgencmd', 'measure_temp']).replace("temp=", "")
    clock_str = run_cmd(['vcgencmd', 'measure_clock', 'arm'])
    clock_mhz = "0"
    if '=' in clock_str:
        try:
            clock_mhz = f"{int(clock_str.split('=')[1]) / 1_000_000:.0f}"
        except Exception:
            pass

    ext5v = pmic.get('EXT5V_V', 'N/A')
    core_v = pmic.get('VDD_CORE_V', 'N/A')
    core_a = pmic.get('VDD_CORE_A', 'N/A')

    now = time.strftime("%Y-%m-%d %H:%M:%S")

    print(f"{BOLD}=================================================================={RESET}")
    print(f" {CYAN}{BOLD}⚡ 라즈베리파이 5 & OpenCR 배터리 통합 진단{RESET}  ({now})")
    print(f"{BOLD}=================================================================={RESET}")

    # 1. OpenCR 로봇 주행 배터리 상태
    print(f"{BOLD}[ 1. OpenCR 로봇 주행 배터리 상태 (3S Li-Po) ]{RESET}")
    bat = get_opencr_battery()
    if bat['status'] == 'ok':
        v = bat['voltage']
        pct = bat['percentage']
        cell_v = v / 3.0

        if v >= 11.8:
            v_color = GREEN
            state_str = f"{GREEN}🟢 양호 (충분한 주행 전력){RESET}"
        elif v >= 11.1:
            v_color = YELLOW
            state_str = f"{YELLOW}🟡 보통 (주행 가능, 주의 요망){RESET}"
        else:
            v_color = RED
            state_str = f"{RED}🔴 방전 경고! 즉시 충전 필요{RESET}"

        print(f"  • 배터리 전압        : {v_color}{BOLD}{v:.2f} V{RESET} (셀당 평균 {cell_v:.2f} V)")
        print(f"  • 잔여 배터리 비율   : {v_color}{BOLD}{pct:.1f} %{RESET}")
        print(f"  • 주행 가능 상태     : {state_str}")
        print(f"  • 하드웨어 연결      : {GREEN}✅ OpenCR (/dev/ttyACM0) 통신 정상{RESET}")
    elif bat['status'] == 'stopped':
        print(f"  • 하드웨어 연결      : {GREEN}✅ OpenCR 포트 정상 연결 (/dev/ttyACM0){RESET}")
        print(f"  • 배터리 정보 상태   : {YELLOW}⚠️ {bat['msg']}{RESET}")
    else:
        print(f"  • 배터리 정보 상태   : {RED}❌ {bat['msg']}{RESET}")

    # 2. 라즈베리파이 5 전원 공급 상태 (5V 라인)
    print(f"\n{BOLD}[ 2. 라즈베리파이 5 전원 공급 상태 (5V 입력) ]{RESET}")
    if flags['under_voltage_now']:
        print(f"  • 현재 전압 상태      : {RED}{BOLD}🚨 저전압 경고 (전원 부족! 전원 어댑터 확인 필요){RESET}")
    else:
        print(f"  • 현재 전압 상태      : {GREEN}✅ 정상 (충분한 전원 공급 중){RESET}")

    if flags['under_voltage_past']:
        print(f"  • 부팅 후 저전압 이력 : {YELLOW}⚠️ 과거 저전압 발생 이력 있음 (전압 강하 주의){RESET}")
    else:
        print(f"  • 부팅 후 저전압 이력 : {GREEN}✅ 발생 이력 없음 (안정적){RESET}")

    print(f"  • 외부 입력 전압 (5V) : {BOLD}{ext5v}{RESET} (정상 기준: 4.85V ~ 5.25V)")
    print(f"  • 코어 전압 / 전류    : {core_v} / {core_a}")

    # 3. CPU 온도 및 성능
    print(f"\n{BOLD}[ 3. CPU 온도 및 성능 쓰로틀링 (Throttling) ]{RESET}")
    temp_num = float(temp_str.replace("'C", "")) if "'C" in temp_str else 0.0
    temp_color = GREEN if temp_num < 70 else (YELLOW if temp_num < 80 else RED)
    print(f"  • CPU 현재 온도       : {temp_color}{BOLD}{temp_str}{RESET}")
    print(f"  • ARM 동작 클럭       : {BOLD}{clock_mhz} MHz{RESET}")

    if flags['throttled_now'] or flags['freq_capped_now']:
        print(f"  • 현재 성능 제한      : {RED}{BOLD}🚨 발열로 인한 클럭 다운(쓰로틀링) 동작 중!{RESET}")
    else:
        print(f"  • 현재 성능 제한      : {GREEN}✅ 정상 (클럭 제한 없음){RESET}")

    print(f"  • vcgencmd Raw Code   : {CYAN}throttled={hex(throttled_hex)}{RESET}")

    # 4. 종합 요약
    print(f"\n{BOLD}[ 4. 종합 진단 요약 ]{RESET}")
    if bat['status'] == 'ok' and bat['voltage'] < 11.0:
        print(f"  {RED}{BOLD}⚠️ OpenCR 배터리 전압({bat['voltage']:.2f}V)이 낮습니다. 모터 급가속 시 다운될 수 있으니 충전해 주세요!{RESET}")
    elif flags['under_voltage_now']:
        print(f"  {RED}{BOLD}❌ 라즈베리파이 전원 입력이 부족합니다. 전원 공급 라인을 점검해 주세요!{RESET}")
    elif flags['under_voltage_past']:
        print(f"  {YELLOW}ℹ️ 라즈베리파이에 과거 전압 강하 이력이 있습니다. 급회전/급가속을 주의하세요.{RESET}")
    else:
        print(f"  {GREEN}{BOLD}🎉 로봇 배터리와 라즈베리파이 전원 모두 완벽하게 최적 상태입니다!{RESET}")
    print(f"{BOLD}=================================================================={RESET}")


def main():
    parser = argparse.ArgumentParser(description="라즈베리파이 5 & OpenCR 배터리 통합 진단 도구")
    parser.add_argument("-w", "--watch", action="store_true", help="실시간 연속 모니터링")
    parser.add_argument("-i", "--interval", type=float, default=2.0, help="모니터링 갱신 주기 (초, 기본값: 2.0)")
    args = parser.parse_args()

    try:
        if args.watch:
            while True:
                print_status(clear_screen=True)
                print(f" 실시간 모니터링 중... (종료: Ctrl+C) / 갱신 주기: {args.interval}초")
                time.sleep(args.interval)
        else:
            print_status(clear_screen=False)
            print(f" * 실시간 연속 감시를 원하시면: {CYAN}check_power -w{RESET}")
    except KeyboardInterrupt:
        print("\n모니터링을 종료합니다.")


if __name__ == '__main__':
    main()

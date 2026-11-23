#!/usr/bin/env python3
import time
import subprocess
import json
import os

def read_power():
    v = 5.0
    temp = 0.0
    throttled = '0x0'
    try:
        out = subprocess.check_output(['vcgencmd', 'pmic_read_adc'], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if 'EXT5V_V' in line:
                v = float(line.split('=')[-1].rstrip('V'))
                break
    except Exception:
        pass

    try:
        tout = subprocess.check_output(['vcgencmd', 'measure_temp'], text=True, stderr=subprocess.DEVNULL)
        temp = float(tout.split('=')[-1].rstrip("'C\n"))
    except Exception:
        pass

    try:
        th = subprocess.check_output(['vcgencmd', 'get_throttled'], text=True, stderr=subprocess.DEVNULL)
        throttled = th.split('=')[-1].strip()
    except Exception:
        pass

    # 5V PD 보조배터리 전압 기준 퍼센트 (4.70V ~ 5.15V)
    pct = min(100.0, max(0.0, ((v - 4.70) / (5.15 - 4.70)) * 100.0))

    data = {
        'voltage': round(v, 2),
        'percentage': round(pct, 1),
        'temperature': round(temp, 1),
        'throttled': throttled,
        'timestamp': round(time.time(), 2)
    }
    return data

def main():
    while True:
        try:
            pdata = read_power()
            with open('/dev/shm/pi5_power.json.tmp', 'w') as f:
                json.dump(pdata, f)
            os.replace('/dev/shm/pi5_power.json.tmp', '/dev/shm/pi5_power.json')
        except Exception:
            pass
        time.sleep(1.0)

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
import os
import sys
import time
import subprocess
import signal

ENV = os.environ.copy()
ENV["ROS_DOMAIN_ID"] = "30"
ENV["TURTLEBOT3_MODEL"] = "waffle"
ENV["RMW_IMPLEMENTATION"] = "rmw_cyclonedds_cpp"
ENV["CYCLONEDDS_URI"] = "file:///root/ros2_JTA/config/cyclonedds.xml"
ENV.pop("FASTRTPS_DEFAULT_PROFILES_FILE", None)
ENV.pop("ROS_DISCOVERY_SERVER", None)

MAP_YAML = "/root/ros2_JTA/maps/my_map.yaml"
RVIZ_CONF = "/root/ros2_JTA/config/nav2_default_view.rviz"

print("==========================================================")
print(" 🗺️  [Offline Map Viewer] 2D 지도 활성화 & RViz2 기동")
print("==========================================================")

# 1. 이전 잔류 프로세스 정리
subprocess.run(["pkill", "-9", "-f", "map_server|rviz2"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# 2. Map Server 기동
print("🚀 1단계: Map Server 구동 중...")
map_proc = subprocess.Popen(
    ["ros2", "run", "nav2_map_server", "map_server", "--ros-args", "-p", f"yaml_filename:={MAP_YAML}", "-p", "use_sim_time:=False"],
    env=ENV,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL
)

# 3. Lifecycle 활성화 대기 (최대 5초)
print("⚙️  2단계: 지도 메모리 로딩 및 액티브 전환...")
time.sleep(2.0)

for _ in range(3):
    res_conf = subprocess.run(["ros2", "lifecycle", "set", "/map_server", "configure"], env=ENV, capture_output=True, text=True)
    if "successful" in res_conf.stdout:
        break
    time.sleep(1.0)

time.sleep(0.5)
subprocess.run(["ros2", "lifecycle", "set", "/map_server", "activate"], env=ENV, capture_output=True, text=True)
print("✅ 3단계: 지도가 /map 토픽으로 정상 활성화되었습니다!")

# 4. RViz2 실행
print("🎯 4단계: RViz2 3D 관제창을 화면에 띄웁니다...")
rviz_proc = subprocess.run(["rviz2", "-d", RVIZ_CONF], env=ENV)

# RViz 창 닫을 때 clean-up
print("🛑 RViz2가 종료되어 Map Server를 안전하게 정리합니다.")
map_proc.kill()

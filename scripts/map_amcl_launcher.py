#!/usr/bin/env python3
import os
import sys
import time
import subprocess

ENV = os.environ.copy()
ENV["ROS_DOMAIN_ID"] = "30"
ENV["TURTLEBOT3_MODEL"] = "waffle"
ENV["RMW_IMPLEMENTATION"] = "rmw_cyclonedds_cpp"
ENV["CYCLONEDDS_URI"] = "file:///root/ros2_JTA/config/cyclonedds.xml"
ENV.pop("FASTRTPS_DEFAULT_PROFILES_FILE", None)
ENV.pop("ROS_DISCOVERY_SERVER", None)

MAP_YAML = "/root/ros2_JTA/maps/my_map.yaml"
PARAM_FILE = "/opt/ros/humble/share/turtlebot3_navigation2/param/humble/waffle.yaml"
RVIZ_CONF = "/root/ros2_JTA/config/nav2_default_view.rviz"

print("==========================================================")
print(" 🗺️  [Map + AMCL Launcher] 지도 & 위치추정(라이다 정합) 기동")
print("==========================================================")

# 1. Map Server 기동
print("🚀 1단계: Map Server 구동 중...")
map_proc = subprocess.Popen(
    ["ros2", "run", "nav2_map_server", "map_server", "--ros-args", "-p", f"yaml_filename:={MAP_YAML}", "-p", "use_sim_time:=False"],
    env=ENV
)

# 2. AMCL 기동
print("🚀 2단계: AMCL 위치 추정 노드 구동 중...")
amcl_proc = subprocess.Popen(
    ["ros2", "run", "nav2_amcl", "amcl", "--ros-args", "--params-file", PARAM_FILE, "-p", "use_sim_time:=False"],
    env=ENV
)

time.sleep(2.5)

# 3. Map Server Lifecycle 활성화
print("⚙️  3단계: Map Server Lifecycle 활성화...")
for i in range(5):
    res = subprocess.run(["ros2", "lifecycle", "set", "/map_server", "configure"], env=ENV, capture_output=True, text=True)
    if "successful" in res.stdout:
        break
    time.sleep(1.0)
subprocess.run(["ros2", "lifecycle", "set", "/map_server", "activate"], env=ENV, capture_output=True, text=True)

# 4. AMCL Lifecycle 활성화
print("⚙️  4단계: AMCL Lifecycle 활성화...")
for i in range(5):
    res = subprocess.run(["ros2", "lifecycle", "set", "/amcl", "configure"], env=ENV, capture_output=True, text=True)
    if "successful" in res.stdout:
        break
    time.sleep(1.0)
subprocess.run(["ros2", "lifecycle", "set", "/amcl", "activate"], env=ENV, capture_output=True, text=True)

print("✅ 5단계: Map Server와 AMCL이 모두 정상 활성화되었습니다!")
print("🎯 6단계: RViz2 관제창을 기동합니다...")
rviz_proc = subprocess.run(["rviz2", "-d", RVIZ_CONF], env=ENV)

print("🛑 RViz2가 종료되어 프로세스를 정리합니다.")
map_proc.kill()
amcl_proc.kill()

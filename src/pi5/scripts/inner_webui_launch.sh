#!/bin/bash
source /opt/ros/humble/setup.bash
if [ -f "$HOME/ros2_ws/install/setup.bash" ]; then
    source "$HOME/ros2_ws/install/setup.bash"
fi
export ROS_DOMAIN_ID=30
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI="file://$HOME/cyclonedds.xml"
export TURTLEBOT3_MODEL=waffle
export LDS_MODEL=LDS-01
export PYTHONUNBUFFERED=1

exec python3 "$HOME/jta_web/web_monitor_server.py" 9999

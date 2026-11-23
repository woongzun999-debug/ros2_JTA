#!/bin/bash
source /opt/ros/humble/setup.bash
if [ -f "$HOME/ros2_ws/install/setup.bash" ]; then
    source "$HOME/ros2_ws/install/setup.bash"
fi
export ROS_DOMAIN_ID=30
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI="file://$HOME/cyclonedds.xml"
export TURTLEBOT3_MODEL=waffle

ros2 service call /motor_power std_srvs/srv/SetBool '{data: true}' >/dev/null 2>&1 || true
exec ros2 launch jta_bringup nav2.launch.py "${@}"

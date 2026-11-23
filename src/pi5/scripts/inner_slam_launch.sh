#!/bin/bash
source /opt/ros/humble/setup.bash
if [ -f "$HOME/ros2_ws/install/setup.bash" ]; then
    source "$HOME/ros2_ws/install/setup.bash"
fi
export ROS_DOMAIN_ID=30
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI="file://$HOME/cyclonedds.xml"
export TURTLEBOT3_MODEL=waffle

exec ros2 launch slam_toolbox online_async_launch.py use_sim_time:=False

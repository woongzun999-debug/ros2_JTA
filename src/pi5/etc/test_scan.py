import rclpy
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from rclpy.qos import QoSProfile, ReliabilityPolicy

rclpy.init()
node = Node("scan_check")

def cb(msg):
    ranges = msg.ranges
    def get_min(start, end):
        if start <= end:
            idx = range(start, end+1)
        else:
            idx = list(range(start, 360)) + list(range(0, end+1))
        vals = [ranges[i] for i in idx if 0.12 <= ranges[i] <= 3.5]
        return round(min(vals), 3) if vals else None

    print(f"Front(335~25): {get_min(335, 25)}m")
    print(f"Left(30~85): {get_min(30, 85)}m")
    print(f"Right(275~330): {get_min(275, 330)}m")
    rclpy.shutdown()

sub = node.create_subscription(LaserScan, "/scan", cb, QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT, depth=10))
rclpy.spin(node)

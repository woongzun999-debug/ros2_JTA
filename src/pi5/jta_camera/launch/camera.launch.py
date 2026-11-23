from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    width_arg = DeclareLaunchArgument('width', default_value='640', description='Image width')
    height_arg = DeclareLaunchArgument('height', default_value='480', description='Image height')
    quality_arg = DeclareLaunchArgument('jpeg_quality', default_value='90', description='JPEG compression quality (1-100)')
    fps_arg = DeclareLaunchArgument('target_fps', default_value='15', description='Target streaming FPS')

    camera_node = Node(
        package='jta_camera',
        executable='camera_node',
        name='camera_publisher_node',
        output='screen',
        parameters=[{
            'width': LaunchConfiguration('width'),
            'height': LaunchConfiguration('height'),
            'jpeg_quality': LaunchConfiguration('jpeg_quality'),
            'target_fps': LaunchConfiguration('target_fps'),
            'flip_h': 1,
            'flip_v': 1,
        }],
        respawn=True,
        respawn_delay=2.0
    )

    return LaunchDescription([
        width_arg,
        height_arg,
        quality_arg,
        fps_arg,
        camera_node
    ])

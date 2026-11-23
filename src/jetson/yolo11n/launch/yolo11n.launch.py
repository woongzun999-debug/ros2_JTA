from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    model_arg = DeclareLaunchArgument(
        'model',
        default_value='/root/ros2_ws/yolo11s.pt',
        description='Path to YOLO weights (.pt)'
    )
    imgsz_arg = DeclareLaunchArgument(
        'imgsz',
        default_value='640',
        description='Inference image size (e.g. 480 or 640)'
    )
    conf_arg = DeclareLaunchArgument(
        'conf',
        default_value='0.60',
        description='Confidence threshold'
    )
    use_compressed_arg = DeclareLaunchArgument(
        'use_compressed',
        default_value='true',
        description='Subscribe to compressed image (/camera/image_raw/compressed)'
    )
    publish_annotated_arg = DeclareLaunchArgument(
        'publish_annotated',
        default_value='false',
        description='Publish annotated image (/yolo/image_annotated)'
    )

    yolo_node = Node(
        package='yolo11n',
        executable='yolo_detector',
        name='yolo_detector',
        output='screen',
        parameters=[{
            'model': LaunchConfiguration('model'),
            'imgsz': LaunchConfiguration('imgsz'),
            'conf': LaunchConfiguration('conf'),
            'use_compressed': LaunchConfiguration('use_compressed'),
            'publish_annotated': LaunchConfiguration('publish_annotated')
        }]
    )

    return LaunchDescription([
        model_arg,
        imgsz_arg,
        conf_arg,
        use_compressed_arg,
        publish_annotated_arg,
        yolo_node
    ])

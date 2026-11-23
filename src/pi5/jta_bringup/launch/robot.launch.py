import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, SetEnvironmentVariable, TimerAction
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    tb3_bringup_dir = get_package_share_directory('turtlebot3_bringup')
    jta_camera_dir = get_package_share_directory('jta_camera')

    # 기본 환경 변수 보장 (waffle 모델 및 LDS-01 라이다)
    env_tb3_model = SetEnvironmentVariable('TURTLEBOT3_MODEL', 'waffle')
    env_lds_model = SetEnvironmentVariable('LDS_MODEL', 'LDS-01')
    enable_nav2_arg = DeclareLaunchArgument(
        'enable_nav2',
        default_value='False',
        description='부팅 시 Nav2 자율주행 스택 자동 기동 여부 (SLAM 시 False)',
    )

    # 1. 터틀봇3 기본 브링업 (OpenCR 모터/IMU + 라이다 + 로봇 TF)
    tb3_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(tb3_bringup_dir, 'launch', 'robot.launch.py')
        )
    )

    # 2. JTA 초저지연 CSI 카메라 브링업 (자동 재기동 탑재)
    camera_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(jta_camera_dir, 'launch', 'camera.launch.py')
        ),
        launch_arguments={
            'width': '640',
            'height': '480',
            'jpeg_quality': '90',
            'target_fps': '15',
        }.items()
    )

    # 3. Pi 5 보조배터리/전원 모니터링 노드
    power_node = Node(
        package='jta_bringup',
        executable='pi5_power_node',
        name='pi5_power_node',
        output='screen',
        respawn=True,
        respawn_delay=3.0
    )

    # 4. 현장 USB 스피커 한국어 음성(TTS) 노드 (/vlm/response, /tts/say 구독)
    speaker_node = Node(
        package='jta_bringup',
        executable='speaker_node',
        name='pi5_speaker_node',
        output='screen',
        respawn=True,
        respawn_delay=2.0
    )

    # 5. Nav2 자율주행 스택 자동 기동 (베이스 TF/라이다 안정화 후 5초 지연 기동)
    jta_bringup_dir = get_package_share_directory('jta_bringup')
    nav2_launch = TimerAction(
        period=5.0,
        condition=IfCondition(LaunchConfiguration('enable_nav2')),
        actions=[
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    os.path.join(jta_bringup_dir, 'launch', 'nav2.launch.py')
                )
            )
        ],
    )

    return LaunchDescription([
        env_tb3_model,
        env_lds_model,
        enable_nav2_arg,
        tb3_launch,
        camera_launch,
        power_node,
        speaker_node,
        nav2_launch,
    ])

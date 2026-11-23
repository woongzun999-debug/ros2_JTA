import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction, SetEnvironmentVariable
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _build_costmap_filter_nodes(context, jta_bringup_dir):
    """가상벽(Keepout) 및 서행(Speed) 마스크 서버·라이프사이클 노드를 생성한다."""
    params_file = LaunchConfiguration('params_file').perform(context)
    use_sim_time = LaunchConfiguration('use_sim_time').perform(context).lower() == 'true'
    blank_mask = os.path.join(jta_bringup_dir, 'config', 'blank_filter_mask.yaml')

    keepout_req = LaunchConfiguration('keepout_mask').perform(context)
    speed_req = LaunchConfiguration('speed_mask').perform(context)
    keepout_yaml = keepout_req if os.path.isfile(keepout_req) else blank_mask
    speed_yaml = speed_req if os.path.isfile(speed_req) else blank_mask

    filter_nodes = [
        Node(
            package='nav2_map_server',
            executable='map_server',
            name='keepout_filter_mask_server',
            output='screen',
            parameters=[params_file, {'use_sim_time': use_sim_time, 'yaml_filename': keepout_yaml}],
        ),
        Node(
            package='nav2_map_server',
            executable='costmap_filter_info_server',
            name='keepout_costmap_filter_info_server',
            output='screen',
            parameters=[params_file, {'use_sim_time': use_sim_time}],
        ),
        Node(
            package='nav2_map_server',
            executable='map_server',
            name='speed_filter_mask_server',
            output='screen',
            parameters=[params_file, {'use_sim_time': use_sim_time, 'yaml_filename': speed_yaml}],
        ),
        Node(
            package='nav2_map_server',
            executable='costmap_filter_info_server',
            name='speed_costmap_filter_info_server',
            output='screen',
            parameters=[params_file, {'use_sim_time': use_sim_time}],
        ),
        Node(
            package='nav2_lifecycle_manager',
            executable='lifecycle_manager',
            name='lifecycle_manager_costmap_filters',
            output='screen',
            parameters=[{
                'use_sim_time': use_sim_time,
                'autostart': True,
                'node_names': [
                    'keepout_filter_mask_server',
                    'keepout_costmap_filter_info_server',
                    'speed_filter_mask_server',
                    'speed_costmap_filter_info_server',
                ],
            }],
        ),
    ]
    return filter_nodes


def generate_launch_description():
    """JTA Nav2 자율주행 런치 (AMCL + map_server + Nav2 Stack)."""
    jta_bringup_dir = get_package_share_directory('jta_bringup')
    nav2_bringup_dir = get_package_share_directory('nav2_bringup')

    default_map = os.path.expanduser('~/maps/my_map_261113.yaml')
    default_params = os.path.join(jta_bringup_dir, 'config', 'nav2_params.yaml')

    map_arg = DeclareLaunchArgument(
        'map',
        default_value=default_map,
        description='Nav2에 사용할 2D 지도 YAML 절대 경로',
    )
    params_arg = DeclareLaunchArgument(
        'params_file',
        default_value=default_params,
        description='Nav2 파라미터 YAML 파일 경로',
    )
    use_sim_time_arg = DeclareLaunchArgument(
        'use_sim_time',
        default_value='False',
        description='시뮬레이션 시간 사용 여부 (실로봇은 False)',
    )
    auto_localize_arg = DeclareLaunchArgument(
        'auto_localize',
        default_value='True',
        description='기동 시 AMCL 전역 초기 위치 자동 추정 실행 여부',
    )
    auto_spin_arg = DeclareLaunchArgument(
        'auto_spin',
        default_value='True',
        description='전역 초기화 시 제자리 회전으로 AMCL 수렴 보조 여부',
    )
    enable_patrol_arg = DeclareLaunchArgument(
        'enable_patrol',
        default_value='True',
        description='다중 웨이포인트 순찰 노드(IDLE 대기) 함께 실행 여부',
    )
    patrol_auto_start_arg = DeclareLaunchArgument(
        'patrol_auto_start',
        default_value='False',
        description='기동 즉시 순찰 루프 자동 시작 여부 (기본 False)',
    )
    keepout_mask_arg = DeclareLaunchArgument(
        'keepout_mask',
        default_value=os.path.expanduser('~/maps/keepout_mask.yaml'),
        description='가상벽(KeepoutFilter) 마스크 YAML 경로 (미존재 시 빈 마스크 폴백)',
    )
    speed_mask_arg = DeclareLaunchArgument(
        'speed_mask',
        default_value=os.path.expanduser('~/maps/speed_mask.yaml'),
        description='가상 턱 서행(SpeedFilter) 마스크 YAML 경로 (미존재 시 빈 마스크 폴백)',
    )

    env_tb3_model = SetEnvironmentVariable('TURTLEBOT3_MODEL', 'waffle')

    nav2_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(nav2_bringup_dir, 'launch', 'bringup_launch.py')
        ),
        launch_arguments={
            'map': LaunchConfiguration('map'),
            'params_file': LaunchConfiguration('params_file'),
            'use_sim_time': LaunchConfiguration('use_sim_time'),
        }.items(),
    )

    auto_localizer_node = Node(
        package='jta_bringup',
        executable='auto_localizer',
        name='jta_auto_localizer',
        output='screen',
        condition=IfCondition(LaunchConfiguration('auto_localize')),
        parameters=[{
            'enable_spin': LaunchConfiguration('auto_spin'),
        }],
    )

    patrol_node = Node(
        package='jta_bringup',
        executable='patrol_node',
        name='jta_patrol_node',
        output='screen',
        condition=IfCondition(LaunchConfiguration('enable_patrol')),
        parameters=[{
            'auto_start': LaunchConfiguration('patrol_auto_start'),
        }],
    )

    filter_nodes_action = OpaqueFunction(
        function=lambda context: _build_costmap_filter_nodes(context, jta_bringup_dir)
    )

    return LaunchDescription([
        env_tb3_model,
        map_arg,
        params_arg,
        use_sim_time_arg,
        auto_localize_arg,
        auto_spin_arg,
        enable_patrol_arg,
        patrol_auto_start_arg,
        keepout_mask_arg,
        speed_mask_arg,
        nav2_launch,
        filter_nodes_action,
        auto_localizer_node,
        patrol_node,
    ])

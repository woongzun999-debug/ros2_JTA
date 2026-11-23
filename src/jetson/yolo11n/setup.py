import os
from glob import glob
from setuptools import find_packages, setup

package_name = 'yolo11n'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='ROS2_JW',
    maintainer_email='ROS2_JW@users.noreply.github.com',
    description='Ultra-lightweight YOLO11 real-time detection ROS 2 package for Jetson Orin Nano',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'yolo_detector = yolo11n.detector_node:main',
        ],
    },
)

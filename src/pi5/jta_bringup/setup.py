import os
from glob import glob
from setuptools import find_packages, setup

package_name = 'jta_bringup'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml') + glob('config/*.pgm')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='ROS2_JW',
    maintainer_email='ROS2_JW@users.noreply.github.com',
    description='JTA AMR One-Click Master Bringup Package',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'pi5_power_node = jta_bringup.pi5_power_node:main',
            'speaker_node = jta_bringup.speaker_node:main',
            'auto_localizer = jta_bringup.auto_localizer:main',
            'patrol_node = jta_bringup.patrol_node:main',
        ],
    },
)

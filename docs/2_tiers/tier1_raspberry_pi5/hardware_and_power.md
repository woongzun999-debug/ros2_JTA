# 📋 Raspberry Pi 5 및 하드웨어 입출력 스펙 종합 보고서

- **점검 일시**: 2026-11-23
- **대상 기기**: Raspberry Pi 5 Model B Rev 1.1
- **원격 접속**: `<PI5_IP>:22` (계정: `<PI_USER>`, SSH Key 무인증)
- **주요 역할**: 실로봇 하드웨어 I/O 허브 (CSI 카메라, 2D LiDAR, OpenCR 모터 구동계 직결)

### 📋 문서 버전 관리 (Document Version Control)
| 문서&nbsp;버전 | 개정&nbsp;일자 | 작성자 | 문서&nbsp;상태 | 변경 및 개정 내역 |
| :---: | :---: | :---: | :---: | :--- |
| `v1.0.0` | 26.10.27 | ROS2_JW | Approved | • Pi 5 하드웨어 스펙, OpenCR 직결 및 저전력(1.4GHz) 설정 최초 제정 |
| `v1.2.0` | 26.11.04 | ROS2_JW | Approved | • USB 스피커 추가, 5.2V 5A 벅 컨버터 규격 제정, Linger 자동 기동 및 단일 GND 적용 |
| `v1.3.0` | 26.11.09 | ROS2_JW | Approved | • 5.2V 5A 벅 컨버터 실측 검증(`vcgencmd: 0x0`) 및 USB 1.6A 출력 개방 |
| `v1.4.0~1.4.1` | 26.11.16 | ROS2_JW | Approved | • CycloneDDS Unicast 전환, `640x480` Q90 15 FPS 및 `speaker_node` 볼륨/세레머니 탑재 |
| **`v1.4.9`** | 26.11.23 | **ROS2_JW** | **Approved** | • `/battery_state` `9.0V < V <= 10.5V` 5초 지속 시 `patrol_node` `dock` 자동 복귀 및 `/pi5/battery_state` · `jta-web-monitor.service` 연동 반영 |
| `v2.0.0` | (예정) | ROS2_JW | 계획&nbsp;(Planned) | • 실주행 중 배터리 전압 강하 및 패시브 쿨링 온도 실측 데이터 추가 |

---

## 1. 🖥️ Raspberry Pi 5 하드웨어 물리 사양

| 항목 | 규격 및 부품명 | 점검 결과 / 세부 정보 |
| :--- | :--- | :--- |
| **보드&nbsp;모델** | Raspberry Pi 5 Model B Rev 1.1 | 최신 5세대 라즈베리파이 플래그십 보드 |
| **SoC** | Broadcom BCM2712 | 16nm 공정, 64-bit ARMv8.2-A 아키텍처 |
| **CPU** | Quad-Core ARM Cortex-A76 | • **물리 코어**: 4 코어 (기본 클럭 2.4 GHz)<br>• **실제 운영 코어**: **2코어 활성 (Core 0, 1) / 2코어 오프라인 (Core 2, 3)**<br>• **설정 클럭**: **`arm_freq=1400 MHz` (언더클럭 적용)** |
| **RAM&nbsp;(메모리)** | **8GB LPDDR4X-4267** | 총 **7.8 GiB** (가용 **7.4 GiB**, 사용 중 **358 MiB**, Swap 비활성화) |
| **스토리지** | 16GB MicroSD (`/dev/mmcblk0p2`) | 총 **15 GB** (사용 **8.4 GB**, 여유 **5.1 GB**, 사용률 **63%**) |
| **동작&nbsp;온도** | 온보드 열 관리 센서 | **54.3 °C** (안정적인 패시브 쿨링 동작 중) |
| **무선&nbsp;네트워크** | 온보드 802.11ac Wi-Fi (`wlan0`) | IP: **`<PI5_IP>/24`** (CycloneDDS Unicast Peer 연동) |

---

## 2. 🔌 USB 및 CSI 직결 하드웨어 디바이스 목록

| 장치명 | 연결 포트 | 벤더 및 모델명 / 칩셋 | VID:PID | 통신 속도 / 규격 |
| :--- | :--- | :--- | :--- | :--- |
| **OpenCR 1.0** (제어보드) | `/dev/ttyACM0` | ROBOTIS OpenCR (STMicroelectronics) | `0483:5740` | 1,000,000 bps (1 Mbps CDC ACM) |
| **LDS-01** (2D LiDAR) | `/dev/ttyUSB0` | Silicon Labs CP2102 USB-to-UART | `10c4:ea60` | 115,200 bps (UART) |
| **CSI 카메라** | CSI 포트 (`rp1-cfe`) | Sony IMX219 (RPi Camera Module v2) | On-board | 10-bit Raw Bayer (`SBGGR8_1X8`/`BA81`), 640x480 @ 15fps |

---

## 3. 🐧 호스트 OS 및 런타임 환경

| 항목 | 상세 버전 및 구성 |
| :--- | :--- |
| **호스트&nbsp;OS** | **Ubuntu 24.04.5 LTS (Noble Numbat)** 64-bit (aarch64) |
| **Linux&nbsp;커널** | `6.8.0-1065-raspi` (#69-Ubuntu SMP PREEMPT_DYNAMIC) |
| **컨테이너&nbsp;환경** | **Distrobox (이름: `humble`)**<br>• 베이스 이미지: `docker.io/library/ros:humble-ros-base-jammy` (Ubuntu 22.04 기반)<br>• 시스템 계정 및 디바이스(`/dev`) 무오버헤드 통합 공유 |
| **ROS&nbsp;2&nbsp;배포판** | **ROS 2 Humble Hawksbill** |
| **RMW&nbsp;미들웨어** | `rmw_cyclonedds_cpp` (CycloneDDS Unicast Peer 모드) |
| **ROS&nbsp;도메인** | **`ROS_DOMAIN_ID=30`** |
| **DDS&nbsp;설정&nbsp;파일** | `config/cyclonedds.xml` (`<LAN_SUBNET>` 대역 고정, 노트북 `<LAPTOP_IP>`, 젯슨 `<JETSON_IP>`, 파이 `<PI5_IP>` 피어링) |

---

## 4. ⚡ 전력 최적화 및 안정화 설정 (`/boot/firmware/config.txt`)

5V 3A 단일 보조배터리로 로봇 구동 시 순간적인 피크 전류로 인한 보조배터리 OCP(과전류 보호회로) 차단 및 라즈베리파이 강제 셧다운(Red LED 행)을 방지하기 위해 다음 하드웨어 레벨 저전력 튜닝이 영구 적용되어 있습니다:

```ini
[all]
arm_64bit=1
dtoverlay=vc4-kms-v3d
camera_auto_detect=1
enable_uart=1

# [전원 및 클럭 안정화 튜닝]
arm_freq=1400              # CPU 최대 클럭을 2.4GHz에서 1.4GHz로 언더클럭 (피크 전류 40% 절감)
usb_max_current_enable=1   # 5.2V 5A 벅 컨버터 기반 USB 1.6A 풀 출력 개방 (LiDAR·OpenCR·오디오 동시 구동 안정화)
# (추가로 시스템 부팅 시 CPU 코어 2, 3을 오프라인 처리하여 Core 0, 1 듀얼코어로 전력 마진 확보)
```

---

## 5. 📡 RPi 5 발행/구독 주요 토픽 현황

| 토픽명 | 메시지 타입 | 담당 노드 | 설명 |
| :--- | :--- | :--- | :--- |
| `/camera/image_raw/compressed` | `sensor_msgs/msg/CompressedImage` | `jta_camera` (`camera_node`) | 640x480 JPEG(Q90), 15 FPS 주력 스트림 (`/camera/image_raw`는 On-Demand 발행) |
| `/scan` | `sensor_msgs/msg/LaserScan` | `hlds_laser_publisher` | 360도 2D LiDAR 포인트 클라우드 거리 데이터 |
| `/odom` | `nav_msgs/msg/Odometry` | `turtlebot3_ros` (OpenCR) | 휠 엔코더 기반 로봇 위치 및 주행 속도 피드백 |
| `/imu` | `sensor_msgs/msg/Imu` | `turtlebot3_ros` (OpenCR) | OpenCR 내장 6축 자이로/가속도 센서 융합 데이터 |
| `/battery_state` | `sensor_msgs/msg/BatteryState` | `turtlebot3_ros` (OpenCR) | OpenCR 메인 배터리 전압 모니터링 (`9.0V < V <= 10.5V` 5초 지속 시 `dock` 자동 복귀) |
| `/pi5/battery_state` | `sensor_msgs/msg/BatteryState` | `pi5_power_node` | Pi 5 PMIC 전압·온도·스로틀링 상태(`vcgencmd`) 텔레메트리 발행 |
| `/joint_states` | `sensor_msgs/msg/JointState` | `turtlebot3_ros` (OpenCR) | 좌/우 구동 휠 모터 엔코더 틱 및 관절 각도 |
| `/cmd_vel` (구독) | `geometry_msgs/msg/Twist` | `turtlebot3_ros` (OpenCR) | 젯슨/노트북으로부터 수신되는 주행 이동 명령 |

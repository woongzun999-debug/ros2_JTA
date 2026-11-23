# 📋 Jetson Orin Nano 하드웨어 스펙 및 Docker/ROS 2 환경 진단 보고서

- **점검 일시**: 2026-11-24
- **대상 기기**: NVIDIA Jetson Orin Nano Developer Kit Super
- **원격 접속**: `<JETSON_IP>:2222` (계정: `<JETSON_USER>`, SSH Key 무인증)
- **점검 방식**: 비파괴적(Read-Only) 원격 진단

### 📋 문서 버전 관리 (Document Version Control)
| 문서&nbsp;버전 | 개정&nbsp;일자 | 작성자 | 문서&nbsp;상태 | 변경 및 개정 내역 |
| :---: | :---: | :---: | :---: | :--- |
| `v1.0.0` | 26.10.27 | ROS2_JW | Approved | • Jetson Orin Nano 8GB 하드웨어 및 Docker 인프라 실측 스펙 제정 |
| `v1.2.0` | 26.11.04 | ROS2_JW | Approved | • YOLO11s(`conf=0.60`), Qwen2-VL-2B(FP16), 16GB Swap 및 TUI 모드(`400MB`) 반영 |
| `v1.3.0` | 26.11.10 | ROS2_JW | Approved | • Qwen2-VL 4-bit NF4 경량화(`VRAM 1.42GB`, `10.80s`) 및 YOLO11n 동시 상주 안착 |
| `v1.4.0~1.4.1` | 26.11.16 | ROS2_JW | Approved | • CycloneDDS Unicast(`<LAN_SUBNET>` 고정) 및 45W 단독 전원 + `MAXN_SUPER (Mode 2)` 확정 |
| `v1.4.9` | 26.11.23 | ROS2_JW | Approved | • `mediapipe==0.10.18` 컨테이너 격리 설치(`numpy 1.26.4`·`opencv`·`torch` 유지)<br>• `floor_obstacle_node.py` & `gesture_node.py` 워크스페이스 통합 반영 |
| **`v1.4.10`** | 26.11.24 | **ROS2_JW** | **Approved** | • 호스트 상시 `network-failover.service`(`scripts/network_failover.py`, `install_failover.sh`, `test_failover_status.sh`) & `rotate_screen.sh` 1:1 백업<br>• `dobingda.mp3` Edge-TTS 안내 음성 연동 반영 |
| `v2.0.0` | (예정) | ROS2_JW | 계획&nbsp;(Planned) | • 3cm 의자 다리 Sim2Real VLM 무인 라벨링 파이프라인 탑재 |

---

## 1. 🖥️ 하드웨어 사양 (Hardware Specifications)

| 항목 | 세부 규격 | 점검 결과 / 세부 정보 |
| :--- | :--- | :--- |
| **SoC / 보드** | Jetson Orin Nano DevKit | **NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super** |
| **CPU** | 6-Core ARM Cortex-A78AE | 64-bit v8.2-A, 6 코어 / 6 스레드, 최대 클럭 **1728 MHz** (최저 115.2 MHz) |
| **RAM (메모리)** | **8GB LPDDR5** | 총 **7.3 GiB** (가용 **6.3 GiB**, 사용 중 **1.0 GiB**, Swap 비활성화) |
| **NVMe SSD** | Micron 2400 (512GB) | DRAM-less PCIe Gen4 NVMe, 루트 마운트 `/` (총 **467GB**, 사용 **74GB**, 여유 **370GB**, 사용률 **17%**) |
| **전력 모드 (Power)** | **MAXN_SUPER (Mode 2)** | 최고 성능 프로파일로 설정됨 (`nvpmodel -q`) |
| **네트워크 (Wi-Fi)** | Realtek RTL8822CE | 802.11ac PCIe 무선 LAN 인터페이스 (`wlP1p1s0`: **`<JETSON_IP>`/24**) |
| **네트워크 (이더넷)** | Realtek RTL8111/8168 | Gigabit Ethernet (`enP8p1s0`, 현재 Link Down) |
| **가상 브리지** | L4T USB 브리지 | `l4tbr0` (`<USB_BRIDGE_IP>`) |

---

## 2. 🐧 호스트 OS 및 NVIDIA 드라이버 스택

| 항목 | 상세 버전 및 정보 |
| :--- | :--- |
| **Host OS** | **Ubuntu 24.04.5 LTS (Noble Numbat)** 64-bit (arm64) |
| **Linux Kernel** | `6.8.12-1021-tegra` (#1 SMP PREEMPT Thu Aug 6 2026) |
| **L4T Release** | **R39 (release), REVISION: 2.1** (GCID: 46758480, Tegra oot 커널) |
| **Host CUDA** | **CUDA 13.2.2** (V13.2.86), 드라이버 버전 `595.71.05` |
| **Host 컴파일러** | GCC 13.2.0 (`aarch64-none-linux-gnu-gcc`) |

---

## 3. 🐳 Docker 인프라 현황

- **Docker 엔진**: Community Edition **29.8.1** (containerd v2.3.6, runc 1.5.1)
- **NVIDIA Container Runtime**: `nvidia-container-runtime` 활성화됨 (`--runtime nvidia`)

### 📦 등록된 도커 이미지 (Docker Images)
1. **`my_ros2_robot:humble`** (ID: `bedf6987a8bc`, 크기: **31.1 GB**)
   - 베이스 이미지: `dustynv/ros:humble-desktop-l4t-r36.4.0`
   - 추가 패키지: Nav2, SLAM Toolbox, Turtlebot3 유틸리티, PyTorch, Torchvision, Ultralytics YOLOv8/v11
2. **`dustynv/ros:humble-desktop-l4t-r36.4.0`** (ID: `b8ee30b1ae18`, 크기: **21.1 GB**)
   - NVIDIA JetPack 최적화 ROS 2 Humble 공식 컨테이너 베이스
3. **`ubuntu:22.04`** (ID: `829f6df217bc`, 크기: **109 MB**)

### 🔄 등록된 컨테이너 (Containers)
- **`ros2_humble_dev`** (`my_ros2_robot:humble`):
  - 네트워크: `host` 모드
  - 바인드 마운트:
    - `<JETSON_HOME>/ros2_ws` ➔ `/root/ros2_ws`
    - `/dev` ➔ `/dev`
    - `/tmp/.X11-unix` ➔ `/tmp/.X11-unix`
  - 런타임: `nvidia`
- **`jetson_ros`** (`my_ros2_robot:humble`):
  - 환경변수: `ROS_DOMAIN_ID=30`, `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`, `CYCLONEDDS_URI=file:///tmp/cyclonedds.xml`
  - 바인드 마운트: `config/cyclonedds.xml` ➔ `/tmp/cyclonedds.xml:ro`

---

## 4. 🤖 컨테이너 내부 ROS 2 및 AI 프레임워크 스펙

### 🌐 ROS 2 통신 아키텍처
- **ROS 배포판**: **ROS 2 Humble Hawksbill** Desktop
- **RMW 구현체**: `rmw_cyclonedds_cpp` (CycloneDDS)
- **ROS_DOMAIN_ID**: **`30`**
- **DDS 구성**: **CycloneDDS 유니캐스트 프로파일** ([`config/cyclonedds.xml`](../../../config/cyclonedds.xml), 상세 토폴로지는 [`master_specification.md` 2장](../../1_master/master_specification.md#2-분산-통신-네트워크-인프라-cyclonedds-unicast-peer) 참조)

### 📦 설치된 주요 ROS 2 패키지
- **내비게이션 & SLAM**:
  - `ros-humble-slam-toolbox`
  - `ros-humble-navigation2`
  - `ros-humble-nav2-bringup`
- **로봇 제어**:
  - `ros-humble-turtlebot3-msgs`
  - `ros-humble-turtlebot3-teleop`
  - `ros-humble-turtlebot3-description` (`TURTLEBOT3_MODEL=burger`)
- **비전 / 그래픽스**:
  - `cv_bridge`, `image_transport`, `compressed_image_transport`, `rviz2`

### 🧠 AI / 딥러닝 라이브러리 버전 (`ros2_humble_dev` 컨테이너 실측)
- **PyTorch / Torchvision**: `2.8.0` / `0.23.0` (CUDA 가속)
- **OpenCV / NumPy**: `4.10.0-dev` (`cv2.aruco` 내장) / `1.26.4`
- **Ultralytics YOLO**: `8.4.154`
- **MediaPipe**: `0.10.18` (`--no-deps` 격리 설치로 기존 `numpy 1.26.4`·`opencv`·`torch` 무변경 유지)
- **준비된 모델 가중치**:
  - `yolo11n.pt` (주력 YOLOv11 Nano)
  - `Qwen/Qwen2-VL-2B-Instruct` (4-bit NF4, `/root/ros2_ws/models`)

---

## 5. 📂 Jetson 전용 워크스페이스 및 로봇 코드

### 1) 호스트·컨테이너 동기화 워크스페이스: `<JETSON_HOME>/ros2_ws` ↔ `/root/ros2_ws`
- **`src/yolo11n`**: `yolo_detector` ROS 2 패키지 (GPU 실시간 추론 ➔ `/yolo/detections` 0.1ms JSON)
- **`vlm_service_node.py`**: `Qwen2-VL-2B` 4-bit NF4 상주 브리핑 + `1.2m(h >= 250px)` 사람 근접 `dobingda.mp3`(Pi 5 Edge-TTS 안내 음성) 3초 확정 트리거
- **`floor_obstacle_node.py`**: 전 구간 상시 바닥 장애물 감지(OpenCV LAB/에지 + `0.8s` 잔상) ➔ `/vision/scan` & `/vision/floor_obstacles`
- **`gesture_node.py`**: `DICT_4X4_50` ArUco 카드(`0~10`) + 한 손 펼침(✋)/엄지척(👍) Nav2·순찰 제어 ➔ `/gesture/cmd` & `/gesture/state`

### 2) 호스트 네트워크 페일오버 & 유틸 (`src/jetson/scripts/`)
- **`network-failover.service` (`network_failover.py`)**: 유선(`enP8p1s0`) ↔ 무선(`wlP1p1s0`) 고정 IP `<JETSON_IP>/24` 자동 전환 상시 데몬 (`install_failover.sh`, `test_failover_status.sh` 포함)
- **`rotate_screen.sh`**: TUI(`fbcon`)/X11 화면 회전 헬퍼 유틸

---

## 6. 💡 시스템 특징 및 연동 진단 요약

1. **하드웨어 체급 확인**:
   - 4GB 모델이 아닌 **8GB Orin Nano Super** 모델로 확인되었으며, 잔여 메모리가 약 **6.3 GiB**로 풍부하여 YOLOv8/v11 외에도 경량 LLM(Ollama SLM 모델 등)의 동시 구동이 충분히 가능한 고성능 엣지 AI 사양입니다.
2. **NVMe 저장 공간 여유**:
   - 512GB 고속 NVMe SSD 중 **370GB(83%)**의 넉넉한 여유 공간이 있어 대규모 데이터셋 로깅 및 도커 이미지 확장이 자유롭습니다.
3. **분산 ROS 2 토폴로지 완비**:
   - `ROS_DOMAIN_ID=30`과 CycloneDDS Unicast(`<LAPTOP_IP>`, `<JETSON_IP>`, `<PI5_IP>`) 프로파일이 완벽하게 일치하여 별도의 네트워크 설정 변경 없이 즉시 라즈베리파이 5의 영상/라이다 수신 및 주행 제어가 가능합니다.

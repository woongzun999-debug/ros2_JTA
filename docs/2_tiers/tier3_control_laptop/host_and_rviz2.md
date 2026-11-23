# 📋 관제 노트북(Host PC) 하드웨어 스펙 및 Docker/ROS 2 환경 보고서

- **점검 일시**: 2026-11-23
- **대상 기기**: 관제 노트북 (Local Host PC)
- **IP 주소**: `<LAPTOP_IP>/24` (인터페이스: `wlo1`)
- **시스템 역할**: **Tier 3 (3D 디지털 트윈 관제, RViz2 HMI, 시뮬레이션 및 데이터 허브)**

### 📋 문서 버전 관리 (Document Version Control)
| 문서&nbsp;버전 | 개정&nbsp;일자 | 작성자 | 문서&nbsp;상태 | 변경 및 개정 내역 |
| :---: | :---: | :---: | :---: | :--- |
| `v1.0.0` | 26.10.27 | ROS2_JW | Approved | • 관제 노트북 i7-12700H, 48GB RAM, RTX 4070 및 Docker 관제 스펙 제정 |
| `v1.2.0` | 26.11.04 | ROS2_JW | Approved | • `yolo_monitor_viewer.py` HUD 뷰어, 압축 스트림 디코딩 및 Zero-Dependency 확립 |
| `v1.3.0` | 26.11.05 | ROS2_JW | Approved | • 상주 웹 서비스(`:5555`, `:7777`, `:9999`) 및 Git 데몬(`:9418`) `systemd` 자동 기동 |
| `v1.4.0` | 26.11.16 | ROS2_JW | Approved | • Lichtblick v1.30.0 + `foxglove_bridge(:8765)` 도커/레이아웃 관제 스택 추가 |
| `v1.4.2` | 26.11.17 | ROS2_JW | Approved | • `foxglove-bridge.service` 호스트 `systemd --user` 상시 자동 기동(`:8765`) 등록 |
| **`v1.4.9`** | 26.11.23 | **ROS2_JW** | **Approved** | • WebUI `:9999` Pi 5(`<PI5_IP>:9999`, `jta-web-monitor.service`) 주력 이전 및 `<canvas>`·`dock` 지정·`OpenCR OFF` 반영 |
| `v2.0.0` | (예정) | ROS2_JW | 계획&nbsp;(Planned) | • 실로봇 RViz2 60 FPS 렌더링 및 GPU VRAM 실측치 반영 |

---

## 1. 🖥️ 하드웨어 물리 사양 (Laptop Specifications)

| 항목 | 상세 스펙 | 점검 결과 / 엔지니어링 특징 |
| :--- | :--- | :--- |
| **CPU** | **12th Gen Intel Core i7-12700H** | • **14 코어 / 20 스레드** (6 Performance-cores + 8 Efficient-cores)<br>• 최대 터보 부스트 **4.70 GHz** (기본 2.3 GHz), L3 캐시 24MB<br>• 멀티스레드 기반 RViz2 렌더링, Gazebo 물리 엔진 연산 완벽 수용 |
| **RAM&nbsp;(메모리)** | **48 GB DDR5** | • 총 **46 GiB** (가용 **43 GiB**, 사용 중 **3.4 GiB**, 여유 40 GiB)<br>• 3D 시각화, 방대한 포인트 클라우드 로깅 및 고용량 SLM/LLM 모델 동시 적재 가능 |
| **GPU&nbsp;(외장&nbsp;그래픽)** | **NVIDIA GeForce RTX 4070 Laptop GPU** | • **8 GB GDDR6** VRAM (TGP 최대 80W)<br>• 드라이버: **595.99.02**, CUDA: **13.2**<br>• 하드웨어 OpenGL/Vulkan 가속 및 텐서 코어 탑재 |
| **스토리지&nbsp;(SSD)** | 512GB PCIe NVMe SSD (`/dev/nvme1n1p6`) | • 총 **492 GB** (사용 **234 GB**, 가용 **233 GB**, 사용률 **51%**)<br>• 대용량 ROS 2 Bag 데이터셋 및 도커 이미지 고속 I/O 보장 |
| **네트워크&nbsp;(Wi-Fi)** | Intel Wi-Fi 6 / 6E 무선 어댑터 (`wlo1`) | • IP: **`<LAPTOP_IP>/24`**<br>• 5GHz 고속 무선 링크로 젯슨 및 라즈베리파이와 CycloneDDS Unicast Peer(`rmw_cyclonedds_cpp`, `config/cyclonedds.xml`) 연동 |

---

## 2. 🐧 호스트 운영체제 및 그래픽 드라이버

| 항목 | 세부 정보 |
| :--- | :--- |
| **호스트 OS** | **Linux Mint 22.3 (Zena)** 64-bit (Ubuntu 24.04 LTS Noble 기반) |
| **Linux 커널** | `6.8.0-79-generic` (x86_64) |
| **NVIDIA 드라이버** | NVIDIA UNIX x86_64 Kernel Module **595.99.02** |
| **하드웨어 가속 런타임** | NVIDIA Container Toolkit (`--gpus all` 지원) |

---

## 3. 🐳 Docker 인프라 및 컨테이너 환경

- **Docker 데몬**: Docker Engine 29.8.x (`nvidia-container-toolkit` 완비)

### 📦 주요 도커 이미지 (Docker Images)
1. **`ros2-humble-gazebo:latest`** (ID: `8cd2390d3a50`, 크기: **6.49 GB**)
   - 베이스: `osrf/ros:humble-desktop-full`
   - 구성: ROS 2 Humble, Gazebo 11, TurtleBot3 패키지 전집(`turtlebot3_simulations`, `turtlebot3_msgs`), RViz2, Teleop
2. **`osrf/ros:humble-desktop-full`** (ID: `b0302b39a232`, 크기: **5.37 GB**)
   - OSRF 공식 ROS 2 Humble 데스크톱 풀 패키지 이미지

### 🔄 관제 런처 스크립트: `~/ros2_humble_docker/ros2basic.sh`
- **목적**: 실로봇 운용 시 Gazebo 시뮬레이션 가상 시간 간섭을 제거하고, 순수 RViz2 및 실시간 관제 환경 제공
- **핵심 파라미터**:
  - `--net=host --ipc=host`: ROS 2 DDS 동적 포트 직접 바인딩
  - `--gpus all` + `-e __NV_PRIME_RENDER_OFFLOAD=1 -e __GLX_VENDOR_LIBRARY_NAME=nvidia`: RTX 4070 하드웨어 가속 렌더링
  - `-e ROS_DOMAIN_ID=30`: 3개 기기 간 통신 도메인 일치
  - `-v <REPO_ROOT>:/root/ros2_JTA`: 프로젝트 워크스페이스 실시간 양방향 마운트
  - `-e RMW_IMPLEMENTATION=rmw_cyclonedds_cpp -e CYCLONEDDS_URI=file:///root/ros2_JTA/config/cyclonedds.xml`: CycloneDDS 유니캐스트 피어 프로파일 주입

---

## 4. 🌐 분산 ROS 2 통신 설정 (CycloneDDS Unicast Peer)

- **단일 원본(SSOT)**: `ROS_DOMAIN_ID=30`, `rmw_cyclonedds_cpp`, 피어 목록(`<LAPTOP_IP>`, `<JETSON_IP>`, `<PI5_IP>`) 상세는 [`config/cyclonedds.xml`](../../../config/cyclonedds.xml) 및 [`master_specification.md` 2장](../../1_master/master_specification.md#2-분산-통신-네트워크-인프라-cyclonedds-unicast-peer)을 참조합니다.

---

## 5. 🌐 상주 웹 및 네트워크 데몬 서비스 (Port Inventory)

호스트 OS(Linux Mint) 부팅 시 `systemd --user` 데몬에 의해 자동 기동되는 관제 및 개발 인프라 포트 맵:

| 포트 | 프로토콜 / 데몬 | 바인딩 프로세스 | 제공 기능 및 접근 경로 |
| :---: | :---: | :--- | :--- |
| **`5555`** | HTTP (Git Web) | `git-web-viewer.service` | `ros2_JTA` Git 커밋 로그, Diff 및 파일 트리 뷰어 (`http://<LAPTOP_IP>:5555`) |
| **`7777`** | HTTP (Docs) | `md-viewer.service` | `docs/` 중앙 포털·명세서 및 일일 일지 웹 브라우저 뷰어 (`http://<LAPTOP_IP>:7777`) |
| **`9999`** | HTTP (WebUI) | Pi 5 `jta-web-monitor.service` (주력) / 노트북 (선택) | 반응형 3-Tier 관제, 패스스루+`<canvas>` 오버레이(YOLO/바닥/제스처), `dock` 지정, `OpenCR OFF` (`http://<PI5_IP>:9999`) |
| **`9418`** | Git Native Protocol | `git-daemon.service` | 로봇 노드(Pi 5, Jetson)용 고속 무인 Git 동기화 (`git://<LAPTOP_IP>/ros2_JTA`) |
| **`8765`** | WebSocket (`foxglove_bridge`) | `ros2_humble_basic` 컨테이너 내부 | Lichtblick 2D/3D 맵·TF·Nav2 Goal·SLAM 통합 관제 브리지 (`ws://localhost:8765`) |

---

## 6. 🧭 RViz2 대체 경량 2D/3D 관제: Lichtblick + `foxglove_bridge`

* **서버 (Docker `ros2_humble_basic`)**: `ros-humble-foxglove-bridge` (v3.6.0)가 `ros2-humble-gazebo:latest` 이미지에 영구 커밋되어 있으며, CycloneDDS 환경에서 `port:=8765`로 기동합니다.
* **클라이언트 (Host OS)**: `/usr/bin/lichtblick` (v1.30.0)에서 `ws://localhost:8765`로 접속합니다.
* **전용 레이아웃**: `config/lichtblick_nav2_slam.json` (Import from file)
  * 2D 탑뷰 고정(`perspective: false`, `followTf: odom` / SLAM·Nav2 시 `map`), `/scan`, `/map`, Costmap, `/plan`, `/goal_pose`, `/initialpose`, 카메라, Teleop, `🛑 비상정지`(`/cmd_vel` 0), `⛔ Nav2 주행 취소`(`/navigate_to_pose/_action/cancel_goal`) 통합 제공.

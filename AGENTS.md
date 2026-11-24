# 🤖 ros2_JTA Harness Constitution (`AGENTS.md`)

- **점검일**: 2026-11-24

`ros2_JTA` (3-Tier 분산 자율주행 및 비전·음성 로봇 시스템)의 최상위 엔지니어링 규약입니다. 모든 코드 수정, 배포, 디버깅 및 문서 작업은 본 헌법과 Tier별 하위 `AGENTS.md`를 따릅니다.

---

## 🚨 3대 코어 강령 (Core Triad)

### 🛑 [제1강령] Hard Brake & 선(先) 컨펌
- **독단 실행 금지**: 기능 추가·문제 해결·수정 요청 시 임의로 파일을 고치거나 원격 명령을 때리지 않는다.
- **표준 절차**: **[① 원인 분석 ➔ ② 2~3가지 선택 옵션(A/B/C) ➔ ③ 형님 컨펌]** 후 실행한다.
- 🟢 **자율 실행 (무질의 가능)**: `read_file` 열람, 코드 검색, `python3 scripts/validate_ros2_harness.py`, 경미한 로컬 UI/오타 수정, 일지·문서 갱신, 로컬 커밋.
- 🔴 **선제 승인 필수**: 원격 기기(Pi 5, Jetson) 배포·기동, 실물 모터 주행(`/cmd_vel`)·전원 제어, 토픽 계약·DDS 설정 변경, 파괴적 삭제(`rm`, `git reset --hard`), 시스템 패키지 설치, 원격 Git 푸시(`git push`).

### 🛡️ [제2강령] Pre-Clean & Zero Hallucination
- **자원 격리**: 수정·빌드·배포 전 타깃 기기 잔류 프로세스(`pgrep -fa` / `ps aux`)를 확인하고 안전 정지(`pkill -f` / E-Stop) 후 착수한다. 시리얼(`/dev/ttyACM*`, `/dev/ttyUSB*`), 카메라(`/dev/video*`), 공유 메모리 충돌을 원천 차단한다.
- **팩트 우선**: 미확인 경로·가설·미실측 수치를 지어내지 않는다. `ps`, `free`, `dmesg`, 로그 등 실측 근거만 보고한다.

### 📝 [제3강령] Clean Engineering & 문서 표준
- **Author 표기**: 모든 기술 문서(`docs/`, `README.md`, 일일 일지)의 작성자는 **`ROS2_JW`** 단독 표기.
- **일일 일지 순수성**: `docs/3_daily_review/YYYY-MM-DD_핵심주제.md`에는 순수 ROS 2 로봇 시스템 엔지니어링 내역만 기록한다.
- **마감 동기화**: 마감 시그널 시 **[코드 + `README.md`/`docs/` + 일일 일지 + `AGENTS.md` 계층 & `validate_ros2_harness.py` 정합성]** 4종을 단일 마일스톤 커밋으로 묶는다.
- **GitHub 원격 푸시 사전 감사 (Pre-Push Gate)**: 원격(`git push`) 연동·푸시 전 **OPSEC(실명·직장·내부 정보)**, **하드코딩 PIN·시크릿·토큰**, **대용량 파일(`*.pt`, 50MB 이상) 및 `.gitignore` 누락** 전수 스캔을 반드시 선행한다.

---

## 1. 3-Tier 분산 토폴로지 & 통신 환경

| Tier | 기기명 | IP&nbsp;/&nbsp;SSH | 계정 | 실행&nbsp;환경 | 소스&nbsp;위치 | 주요 역할 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Tier&nbsp;1** | **Raspberry Pi 5** (+ OpenCR 1.0) | `<PI5_IP>:22` | `<PI_USER>` | 호스트(카메라) + Distrobox `humble` | `src/pi5/` | CSI 카메라(`jta_camera`), LDS-01 LiDAR, OpenCR(`/dev/ttyACM0`), USB 스피커 |
| **Tier&nbsp;2** | **Jetson Orin Nano** | `<JETSON_IP>:2222` | `<JETSON_USER>` | Docker `my_ros2_robot:humble` | `src/jetson/` | GPU 가속 `yolo11s`(표준)/`yolo11n`, `Qwen2-VL-2B` VLM(순수 On-Demand), 상시 바닥장애물(`/vision/scan`), ArUco·한손 제어(`/gesture/cmd`) |
| **Tier&nbsp;3** | **Control Laptop** | `<LAPTOP_IP>` | `<LAPTOP_USER>` | Docker `ros2basic.sh` | `src/laptop/` | Lichtblick(`:8765`, `foxglove-bridge.service`) / RViz2 관제, WebUI(`:9999` Nav2/SLAM·`/map`·순찰), Git(`:5555`/`:9418`), Docs(`:7777`) |

* **ROS 2 / DDS**: ROS 2 Humble, `ROS_DOMAIN_ID=30`, **CycloneDDS Unicast Peer (`RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`, `config/cyclonedds.xml`)** 표준 (`config/fastdds_unicast.xml`은 레거시/폴백용).

---

## 2. ROS 2 토픽 인터페이스 계약 (Topic Contracts)

| 토픽명 | 메시지 타입 | 발행자 (Pub) | 구독자 (Sub) | QoS / 제약사항 |
| :--- | :--- | :--- | :--- | :--- |
| `/camera/image_raw` | `sensor_msgs/msg/Image` | Tier 1 (`jta_camera`) | Tier 2 (옵션) | `depth=1`, `bgr8`, `640x480`, `frame_id='camera_link'`, On-Demand 발행 |
| `/camera/image_raw/compressed` | `sensor_msgs/msg/CompressedImage` | Tier 1 (`jta_camera`) | Tier 2 (`yolo11n`/VLM), Tier 3 (WebUI/HUD) | `depth=1`, `format='jpeg'`, Quality 90 |
| `/scan` | `sensor_msgs/msg/LaserScan` | Tier 1 (`hlds_laser_publisher`) | Tier 1/2/3 | **반드시 `ReliabilityPolicy.BEST_EFFORT`** (`depth=10`) |
| `/cmd_vel` | `geometry_msgs/msg/Twist` | Nav2 / 회피 노드 | Tier 1 (`turtlebot3_node`) | `depth=10`, 종료 시 `0.0` 속도 5회 이상 발행 |
| `/yolo/image_annotated` | `sensor_msgs/msg/Image` | Tier 2 (`yolo_detector`) | Tier 3 (RViz2) | `depth=1`, `bgr8` (옵션 발행) |
| `/yolo/image_annotated/compressed` | `sensor_msgs/msg/CompressedImage` | Tier 2 (`yolo_detector`) | Tier 3 (옵션) | `depth=1`, `format='jpeg'`, Quality 75 |
| `/yolo/detections` | `std_msgs/msg/String` | Tier 2 (`yolo_detector`) | Tier 2 (VLM), Tier 1 (`patrol_node`), Tier 3 (WebUI) | `depth=10`, JSON 포맷 (`class_name`, `conf`, `box`, `center`, `size`) |
| `/yolo/zone_enable` | `std_msgs/msg/Bool` | Tier 1 (`web_monitor_server` 마스터) | Tier 2 (`yolo_detector`), Tier 1 (`patrol_node`) | `depth=10`, 사람탐지 구역 ON/OFF 제어 |
| `/pi5/battery_state` | `sensor_msgs/msg/BatteryState` | Tier 1 (`pi5_power_node`) | Tier 1/3 (WebUI/HUD) | `depth=10`, Pi 5 5V PD 전압·온도 |
| `/pi5/system_state` | `std_msgs/msg/String` | Tier 1 (`pi5_power_node`) | Tier 1/3 (WebUI) | `depth=10`, JSON 실측 `throttled` 비트마스크·전압·온도 (1Hz) |
| `/jetson/system_state` | `std_msgs/msg/String` | Tier 2 (`yolo_detector`) | Tier 1/3 (WebUI) | `depth=5`, JSON `/proc/meminfo` 실측 RAM·Swap·온도 (0.5Hz) |
| `/patrol/status` | `std_msgs/msg/String` | Tier 1 (`jta_patrol_node`) | Tier 3 (WebUI `/map`) | `depth=10`, JSON 순찰 상태 (`IDLE`/`RUNNING` 등), `/patrol/set_enabled` 서비스 연동 |
| `/lidar_enable` | `std_msgs/msg/Bool` | 관제/제어 노드 | Tier 1 (`lidar_manager`) | `depth=10`, LiDAR 모터 ON/OFF |
| `/play/sound` | `std_msgs/msg/String` | Tier 2 (YOLO 트리거), Tier 3 (WebUI) | Tier 1 (`speaker_node`) | `depth=10`, 파일 경로 또는 별칭(`dobingda` Edge-TTS 안내 음성, `neon`) |
| `/speaker/volume_set` | `std_msgs/msg/Int32` | Tier 3 (WebUI) | Tier 1 (`speaker_node`) | `depth=10`, 0~100% 하드웨어 DAC 게인 제어 |
| `/speaker/volume_state` | `std_msgs/msg/Int32` | Tier 1 (`speaker_node`) | Tier 3 (WebUI) | `depth=10`, 현재 볼륨 텔레메트리 |
| `/vlm/trigger` | `std_msgs/msg/String` | Tier 3 (WebUI) | Tier 2 (`vlm_service_node`) | `depth=10`, VLM 수동 브리핑·채팅 트리거 |
| `/vlm/response` | `std_msgs/msg/String` | Tier 2 (`vlm_service_node`) | Tier 1 (`speaker_node`), Tier 3 (WebUI) | `depth=10`, 정제된 VLM 음성 브리핑 텍스트 (TTS 발화) |
| `/vlm/chat_query` | `std_msgs/msg/String` | Tier 3 (CLI `qss`) | Tier 2 (`vlm_service_node`) | `depth=10`, CLI 전용 조용한 VLM 질의 (스피커 미발화) |
| `/vlm/chat_response` | `std_msgs/msg/String` | Tier 2 (`vlm_service_node`) | Tier 3 (CLI `qss`) | `depth=10`, CLI 전용 VLM 텍스트 응답 |
| `/tts/say` | `std_msgs/msg/String` | Tier 2/3 | Tier 1 (`speaker_node`) | `depth=10`, 정제기 경유 한국어 TTS 발화 |
| `/vision/scan` | `sensor_msgs/msg/LaserScan` | Tier 2 (`floor_obstacle_node`) | Tier 1 (`nav2 local_costmap` `vision_obstacle_layer`) | **`BEST_EFFORT`** (`depth=5`), 전방 `±33°` 67빔 바닥 장애물 가상 라이다 (`confirm=4f`, `hold=0.0s`, `pad=±3cm`) |
| `/vision/floor_obstacles` | `std_msgs/msg/String` | Tier 2 (`floor_obstacle_node`) | Tier 1/3 (WebUI `<canvas>`) | `depth=5`, JSON (`roi_top_ratio`, `confirmed_beams`, `min_dist`, `boxes`) |
| `/gesture/cmd` | `std_msgs/msg/String` | Tier 2 (`gesture_node`) | Tier 1 (`patrol_node`, `web_monitor_server`) | `depth=10`, JSON (`ESTOP`, `GOTO_WP` ArUco `0~10` / `NAV2_START` 👍 / `NAV2_STOP` ✋) |
| `/gesture/state` | `std_msgs/msg/String` | Tier 2 (`gesture_node`) | Tier 1/3 (WebUI `<canvas>`) | `depth=10`, JSON 실시간 홀드 진행률 (`0.15s` 주기) |

---

## 3. 6단계 표준 작업 루프

1. **Plan**: 변경 Tier(`src/pi5`, `src/jetson`, `src/laptop`, `config/`) 식별, 하위 `AGENTS.md` 확인. 오류 시 임의 `sleep()`/버퍼 증설 등 대증요법을 금하고 `[DDS ➔ OS/포트 ➔ QoS ➔ 앱 로직]` 순으로 근본 원인 규명.
2. **Pre-Clean**: 타깃 기기 실행 프로세스 확인(`pgrep -fa`) 및 안전 정지(`pkill -f` / E-Stop) 후 작업 착수.
3. **Implement**: 한글 주석·문서 무결성 유지, `try...finally` 자원 정리 보장, **Laziness Protocol**(불필요한 보일러플레이트·신규 헬퍼 남발 금지, 최소 Diff).
4. **Verify**: 수정 직후 `python3 scripts/validate_ros2_harness.py` 실행 및 통과 확인.
5. **Sync & Ops**: 사전 승인 후 `.agents/skills/` 런북 및 `scripts/` 도구로 원격 배포·진단.
6. **Document**: 마감 시 **[코드 + `README.md`/`docs/` + 일일 일지 + `AGENTS.md` 계층 & 검증기 정합성]** 단일 커밋 동기화.

---

## 4. 점진적 컨텍스트 참조 (Progressive Disclosure)

상세 아키텍처·트러블슈팅·로드맵은 필요 시에만 열람합니다:
* 문서 중앙 포털: `docs/INDEX.md`
* 통합 시스템 명세: `docs/1_master/master_specification.md`
* 차기 로드맵: `docs/1_master/architecture_roadmap_v2.md`
* Tier별 상세: `docs/2_tiers/tier1_raspberry_pi5/`, `tier2_jetson_orin_nano/`, `tier3_control_laptop/`

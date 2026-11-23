# 📅 일일 개발 일지 — 2026-11-16 (2차): CycloneDDS 3-Tier 전환, `jta_bringup` Nav2 통합 및 WebUI 원격 제어

- **작성자**: `ROS2_JW`
- **작업 일시**: 2026-11-16 (오후~저녁)
- **핵심 목표**: FastDDS Discovery Server 단일 장애점 제거를 위한 **CycloneDDS Unicast Peer 3-Tier 전면 전환**, `jta_bringup` Nav2 패키지·전역 초기위치 추정 통합, 및 **`:9999` WebUI PIN 인증 기반 Nav2/SLAM 상호 인터락 제어** 구축

---

## 1. 🌐 3-Tier CycloneDDS Unicast Peer 전면 전환

### 1.1 설정 및 환경 통일
* **설정 신설**: `config/cyclonedds.xml`
  * `AllowMulticast=false` + 3-Tier Unicast Peers(`127.0.0.1`, `<LAPTOP_IP>`, `<JETSON_IP>`, `<PI5_IP>`), `MaxAutoParticipantIndex=100`, `MaxMessageSize=65500B`.
* **3-Tier 패키지·런치·자동기동 일괄 반영**:
  * **Tier 1 (Pi 5)**: Distrobox `humble`에 `ros-humble-rmw-cyclonedds-cpp` (`1.3.5`) 설치, `~/.bashrc` 및 `<PI_HOME>/inner_robot_launch.sh` 전환(FastDDS Discovery Server 데몬 제거).
  * **Tier 2 (Jetson)**: `ros2_humble_dev` 내부 `~/.bashrc` 및 `~/ros2_ws/launch_yolo11n.sh` 전환.
  * **Tier 3 (Laptop)**: `ros2-humble-gazebo:latest` 이미지에 `ros-humble-rmw-cyclonedds-cpp` 설치 후 `docker commit`, `ros2basic.sh`, `scripts/`, `web_monitor_server.py`, `yolo_monitor_viewer.py` 일괄 반영.

### 1.2 트러블슈팅 및 실측 검증
1. **`SocketReceiveBufferSize min="10MB"` 도메인 생성 실패 해결**: OS `rmem_max` 제한(Pi 5 `425KB`, Laptop `8MB`)으로 `rmw_create_node` 실패 ➔ 해당 강제 옵션 제거 후 정상 기동.
2. **Jetson APIPA 인터페이스 격리 해결**: Jetson 무선랜 `wlP1p1s0`(`<APIPA_IP>`)가 UP 상태라 유선랜 `enP8p1s0`(`<JETSON_IP>`) 대신 바인딩되는 현상 발견 ➔ `config/cyclonedds.xml`에 `<NetworkInterface address="<LAN_SUBNET>"/>` 추가하여 `<LAN_SUBNET>` 대역 고정.
3. **3-Tier 실측 (`ros2 topic hz`)**:
   * 전체 11개 노드 단일 도메인(`ROS_DOMAIN_ID=30`) 인식 완료
   * `/scan`: **4.15 Hz**, `/camera/image_raw/compressed`: **14.99 Hz**, `/yolo/detections`: **14.95 Hz** (프레임 드롭 없이 1:1 추론·발행).

---

## 2. 🧭 `jta_bringup` Nav2 패키지 통합 & Global Localization

* **`nav2.launch.py` & `config/nav2_params.yaml` 신설**:
  * 기본 맵 `<PI_HOME>/maps/my_map_261113.yaml`, `use_sim_time:=False`, `TURTLEBOT3_MODEL=waffle`.
  * `scripts/run_pi5_nav2.sh`가 `ros2 launch jta_bringup nav2.launch.py`를 호출하도록 연동.
* **전역 초기 위치 추정 노드 (`jta_bringup/auto_localizer.py`)**:
  * 기동 시 `/reinitialize_global_localization` 자동 호출.
  * `auto_spin:=true` 옵션 시 제자리 회전으로 AMCL 공분산 수렴 후 `/cmd_vel` `0.0` 5회 발행 정지 가드레일 보장 (전원 직결 안전을 위해 기본값 `False`).
  * 정지 상태에서도 `/request_nomotion_update` 서비스 호출로 `map -> base_scan` TF 생성 확인.
* **부팅 정책**: `robot.launch.py`의 `enable_nav2` 기본값은 `False`로 유지하여 부팅 시에는 베이스만 가볍게 기동하고, `:9999` WebUI에서 필요 시 선택 기동하도록 분리.

---

## 3. 📱 `:9999` WebUI Nav2/SLAM 원격 제어 & Lichtblick 뷰 고정

* **WebUI 백엔드 (`src/laptop/web/web_monitor_server.py`)**:
  * `POST /api/nav_mode` (`nav2_start`, `nav2_stop`, `slam_start`, `slam_stop`) 신설 및 `JTA_WEB_PIN` (환경변수 인증, `hmac.compare_digest`) 적용.
  * **상호 인터락 & SSH 자폭 방지**: `pre_clean`과 `setsid -f` 기동을 별도 SSH 호출로 분리(단일 커맨드 내 `pkill -f` 자폭 `rc=255` 해결)하고, 컨테이너 내 UID 불일치 우회를 위해 `ssh -F /dev/null` 적용.
  * `/api/health`에 ROS 2 그래프 기반 실시간 `nav_mode` (`NAV2` / `SLAM` / `IDLE`) 추가.
* **WebUI 프론트엔드 (`src/laptop/web/static/index.html`)**:
  * 영상 스트림 바로 아래에 **자율주행 · SLAM 제어 패널**(4열 버튼 + 실시간 모드 배지 + PIN 입력 모달) 배치.
* **Lichtblick 레이아웃 (`config/lichtblick_nav2_slam.json`)**:
  * OpenCV 허프 변환으로 `my_map_261113.pgm` 자체가 수평/수직(`0.0° ~ -1.16°`)임을 확인하고, `followTf: "odom"`, `followMode: "follow-pose"`로 인한 화면 회전 현상을 `"followTf": "map"`, `"followMode": "follow-none"`으로 고정.

---

## 4. 🛠️ 야간 실주행 테스트, Nav2 튜닝 및 WebUI UX · 안전 개편

* **AMCL 전역 초기 위치 추정 정밀도 향상 (`nav2.launch.py`, `nav2_params.yaml`, `auto_localizer.py`)**:
  * `nav2.launch.py`의 `auto_spin` 기본값을 `'True'`로 복구(배터리 실주행 전환).
  * `nav2_params.yaml` AMCL 튜닝: `max_particles` `2000 → 5000`, `max_beams` `60 → 180`, `z_hit/z_rand` `0.5/0.5 → 0.8/0.2`, `update_min_a` `0.2 → 0.1`.
  * `auto_localizer.py`: 전역 초기화 직후 `/request_nomotion_update` **3회 선행 호출** 추가, 회전 설정 `0.5 rad/s` / 최대 `20.0s` 확장, `stop_robot()` 내 `rclpy.spin_once` + `0.25s` DDS 플러시 대기로 종료 시 잔여 회전 속도 누락 방지.
* **Costmap 과포화(89%) 및 장애물 회피 튜닝 (`nav2_params.yaml`)**:
  * `/local_costmap/costmap` 실측 시 전체 `60×60` 셀 중 **89%(`3,211/3,600`)가 비용으로 차 있던 현상** 진단 및 해결.
  * `local_costmap`: 중복 `voxel_layer` 제거 후 `obstacle_layer` 단일화(`raytrace_max_range: 3.0`, `obstacle_max_range: 2.5`), `robot_radius: 0.20`, `inflation_radius: 0.35`, `cost_scaling_factor: 5.0`.
  * `global_costmap`: `use_sim_time: False` 통일, 중복 `voxel_layer` 제거, `robot_radius: 0.22`, `inflation_radius: 0.40`, `cost_scaling_factor: 5.0`.
  * `controller_server`: `BaseObstacle.scale: 0.25`로 과도한 우회와 장애물 충돌 사이의 균형점 확보.
* **`:9999` WebUI 안전 · 가용성 · 레이아웃 개편 (`web_monitor_server.py`, `static/index.html`)**:
  * **재연결 상태 복구 버그 수정**: `watchdog_callback`에서 Pi 5 재연결(`ONLINE`) 시 `전압 쓰로틀링(throttled)`이 `'NO CONNECT'`로 고착되던 문제 해결.
  * **비상 정지 · 재탐색 · 전체 초기화 무비밀번호 즉시 실행**: `nav2_stop`, `slam_stop`, `relocalize`, `full_reset` 액션은 PIN 인증을 면제하여 즉각 대응 가능하도록 개선 (`nav2_start`, `slam_start`는 PIN 유지).
  * **신규 제어 액션 추가**:
    * **🎯 위치 찾기 (`relocalize`)**: Nav2 유지 상태에서 `auto_localizer(enable_spin:=true)`만 재실행.
    * **🔄 주행 전체 초기화 (`full_reset`)**: OpenCR 즉시 정지(`/cmd_vel 0.0` × 5 + 토크 차단) ➔ Pi 5 Nav2/SLAM 프로세스 정리 ➔ 노트북 `foxglove_bridge` 재기동으로 유령 `/cmd_vel` 퍼블리셔 및 `/tf_static` 누락 일괄 초기화.
  * **UI 배치 및 갱신 주기 개편**: 우측 최상단에 **Jetson Orin Nano** 카드 배치 ➔ 바로 아래 **자율주행 · SLAM 제어 패널**(3×2 그리드 + **🔒 모터 락** 통합 + 하단 전체 초기화) 배치, 영상 하단에 **모니터 갱신 주기 바**(초기값 **`1초`**) 이동.

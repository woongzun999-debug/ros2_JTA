# 📅 일일 개발 일지 — 2026-11-16: RViz2 대체 Lichtblick 관제 구축 및 LDS-01 라이다 락업 복구

- **작성자**: `ROS2_JW`
- **작업 일시**: 2026-11-16 (오후)
- **핵심 목표**: RViz2 없이 2D 맵·TF·Nav2 Goal·SLAM을 통합 관제할 수 있는 `Lichtblick + foxglove_bridge` 환경 구축 및 실물 라이다 스캔 이상 원인 규명·복구

---

## 1. 🛠️ RViz2 대체 관제 스택 구축 (`Lichtblick + foxglove_bridge`)

### 1.1 Docker 브리지 영구 반영 (`ros2-humble-gazebo:latest`)
- `ros2_humble_basic` 컨테이너 내부에 `ros-humble-foxglove-bridge` (v3.6.0) 설치 및 `docker commit`으로 `ros2-humble-gazebo:latest` 이미지에 영구 저장.
- CycloneDDS(`ROS_DOMAIN_ID=30`, `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`, `CYCLONEDDS_URI=file:///root/ros2_JTA/config/cyclonedds.xml`) 환경에서 `port:=8765` (`0.0.0.0:8765`) WebSocket 브리지 기동 확인.

### 1.2 호스트 Lichtblick v1.30.0 및 전용 레이아웃 구축
- Linux Mint 22.3 호스트에 `Lichtblick v1.30.0` (`/usr/bin/lichtblick`) 설치 완료.
- 프로젝트 전용 레이아웃 파일 `config/lichtblick_nav2_slam.json` 작성 및 JSON 문법 검증:
  - **2D 탑뷰 고정 (`3D!nav2slam`)**: `perspective: false`, `followTf: "odom"` (SLAM/Nav2 시 `map` 전환), Grid, TF 라벨, `/scan`, `/map`, `/global_costmap/costmap`, `/local_costmap/costmap`, `/plan`, `/robot_description` ON.
  - **Nav2 Publish 매핑**: `/goal_pose` (`PoseStamped`), `/initialpose` (`PoseWithCovarianceStamped`).
  - **우측 보조 패널**: 카메라(`/camera/image_raw/compressed`), Teleop(`/cmd_vel`), **`🛑 비상정지 (STOP)`** (`/cmd_vel` 0 발행), **`⛔ Nav2 주행 취소`** (`/navigate_to_pose/_action/cancel_goal` 서비스 호출).

---

## 2. 🔍 트러블슈팅 및 실측 복구 기록

### 2.1 `TRANSIENT_LOCAL` `/tf_static` 미수신으로 인한 `/scan` 변환 누락 해결
- **증상**: Lichtblick `3D` 패널에서 `Missing transform from frame <base_scan> to frame <odom>` 경고 발생.
- **원인**: `foxglove_bridge` 기존 세션에 이전 클라이언트의 `/tf_static` 구독이 남아 있어, 새 세션 연결·레이아웃 재로드 시 `TRANSIENT_LOCAL` 초기 메시지가 재전송되지 않음.
- **조치**: `foxglove_bridge` 프로세스 재기동으로 클린 세션 생성 ➔ `/tf`, `/tf_static`, `/scan` 정상 구독 및 연결 확인.

### 2.2 LDS-01 라이다 전 방향 `1.617m` 고정 출력(하드웨어 락업) 규명 및 복구
- **증상**: 라이다 모터는 물리적으로 회전 중이나, `/scan` 토픽이 RViz2와 Lichtblick 양쪽 모두에서 완벽한 원형(`1.617m`)으로만 출력됨.
- **실측 진단**:
  - 연속 3프레임 측정 결과 타임스탬프만 5Hz로 갱신되고 `min: 1.617, max: 1.617, unique_cnt: 1, intensity[0]: 1585.0`으로 고정.
  - Pi 5에서 `hlds_laser_publisher` 노드 완전 종료 후 새 프로세스 재기동 시에도 동일 증상 지속 ➔ 소프트웨어/DDS 문제가 아닌 **LDS-01 내부 센서/USB-UART 보드 하드웨어 락업**으로 확정.
- **조치 및 검증**:
  - Pi 5의 LDS-01 USB 케이블 물리 재결착(`/dev/ttyUSB0` 재인식) 후 `hlds_laser_publisher` 재기동.
  - `/scan` 재측정 결과 **`len: 360, nonzero: 287, min: 0.099m, max: 4.169m, unique_cnt: 266`** 확인 ➔ 실제 방 벽/장애물 윤곽 정상 복구 완료.

---

## 3. 📌 차기 예정 작업
1. CycloneDDS 전환 변경분 및 `config/lichtblick_nav2_slam.json` 정리 후 단일 커밋.
2. Lichtblick 2D 뷰(`Display frame: map`) 기반 SLAM 맵핑 및 `waffle_nav2.yaml` 실물 장애물 회피 주행 검증.

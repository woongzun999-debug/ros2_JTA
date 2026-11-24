# 📝 ros2_JTA 프로젝트 변경 이력 (CHANGELOG)

모든 주요 변경 사항과 기능 릴리즈 내역은 본 문서와 [마스터 스펙 문서](docs/spec/robot_system_master_specification.md)에 기록됩니다.

---

## [Unreleased / Roadmap] - 차기 개발 계획

### 🎯 [v1.2.0 계획] - SLAM / Nav2 정밀 자율주행 및 온디바이스 에지 안정화
- **Tier 1 (RPi 5)**: 작업장 현장 카메라 렌즈 수동 초점(Focus) 튜닝 및 조명 맞춤형 노출 최적화.
- **Tier 2 (Jetson Orin Nano)**: LDS-01 2D 라이다 기반 SLAM 지도 작성 및 Nav2 2~5cm 정밀 자율주행 통합. 8GB 메모리 내에서 YOLO + SLAM 동시 구동 안정성 확보.

### 🎯 [v2.0.0 최종 목표] - 차세대 멀티모달 AI 에이전틱 로봇 (YOLO + VLM + LLM)
- **Tier 2 (Jetson)**: 온디바이스 초경량 VLM(Moondream2 / Qwen2-VL 2B) 탑재 ➔ 목적지 도착 후 이벤트 트리거 1초 정밀 사물/상황 분석.
- **Tier 3 (노트북 RTX 4070)**: 로컬 고지능 LLM(Ollama: Qwen2.5 14B / Llama 3.1 8B) 탑재 ➔ Whisper 음성 인식(STT) 및 고음질 한국어 음성(TTS) HMI 연동.
- 사람의 자연어 음성 명령("회의실 테이블 위 파란 컵 가져와") 기반 완전 자율 심부름 미션 완수.

---

## [v1.4.12] - 2026-11-24

### 🎯 핵심 릴리즈 요약
3-Tier 전수 점검에서 식별된 **실주행 안전·비상정지 치명 오류(5종)**와 **로직·성능·현장 운용 고도화(10종)**, 그리고 **바닥 장애물 감도 완화(`confirm=4f`, `raytrace_min_range: 0.0`) 및 사람 감지 카운트다운 HUD·추적 종료 음원 자동 중단·`dobingda` 순차 재생**까지 Pi 5·Jetson 실기 배포 및 검증을 완료했습니다.

### 🚀 세부 변경 내역
- **실주행 안전·비상정지 핫픽스 (`stop.sh`, `patrol_node.py`, `auto_localizer.py`, `run_robot_autostart.sh`, `web_monitor_server.py`, `validate_ros2_harness.py`)**:
  - `src/pi5/utils/stop.sh`: `ROS_DOMAIN_ID=30`, `rmw_cyclonedds_cpp`, `CYCLONEDDS_URI` 명시 + DDS 매칭 대기 + `/motor_power false` + 순찰·Nav2 Goal 취소 + `/cmd_vel=0` 2단계 정지(호스트/컨테이너 겸용).
  - `patrol_node.py` & `auto_localizer.py`: `_scan_cb()` `atan2(sin, cos)` 정규화로 LDS-01 전방 우측(`335~359°`) 사각지대 해소, `goal_seq`/`kind`로 `DOCK_RETURN`/`GOTO_WP` 전환 시 `single_goal_active` 덮어쓰기 및 Goal 응답 대기 Race 차단, 저전압 복귀 중 사람 추종 탈선 차단, `SignalHandlerOptions.NO` 기반 `SIGTERM` 안전 정지, 추적 종료 시 `/play/sound` `"stop"` 자동 발행.
  - `web_monitor_server.py` · `index.html` · `map.html`: 원자적 `POST /api/estop` 신설(회전·순찰·추종·Nav2 취소 + `/cmd_vel=0` + 토크 차단), `cancel_all_navigation()`/`send_waypoint_goal()`의 모든 `patrol_node` 활성 상태 중단, `nav2_start`/`slam_start` 전 `/cmd_vel=0` 선행 발행.
  - `run_robot_autostart.sh`: 중복 실행 검사(`pgrep`)를 부팅 플래그 삭제·`fuser -k` 이전 최상단으로 이동.
  - `validate_ros2_harness.py`: `stop.sh` CycloneDDS·`/motor_power`, `patrol_node` 우측 라이다 정규화·`stop_robot`, `auto_localizer` `stop_robot`, WebUI `/api/estop` 검증 추가(`PASS 78 / 0 FAIL`).
- **Jetson VLM·YOLO·제스처·페일오버 고도화 (`vlm_service_node.py`, `detector_node.py`, `floor_obstacle_node.py`, `gesture_node.py`, `network_failover.py`)**:
  - `vlm_service_node.py`: 백그라운드 25초 공회전 추론 기본 OFF(4-bit VRAM 상주 순수 On-Demand), `camera_callback` 매초 15회 `imdecode` 제거(추론 직전 락 밖 1회 디코딩), non-blocking 락으로 연타 스레드 누적 차단, 사람 근접 사운드 `conf >= 0.50`·`time.monotonic()` 통일.
  - `detector_node.py`: 실기 런치 표준(`yolo11s.pt`, `640`, `0.60`)과 기본값 통일 및 2초 주기 `/jetson/system_state`(`/proc/meminfo` RAM·Swap·온도 실측) 발행 신설.
  - `floor_obstacle_node.py` & `nav2_params.yaml`: 바닥 과민 오탐 완화(`chroma_thresh=18.0`, `luma_thresh=38.0`, `min_area_px=650`, `confirm_frames=4`, 밝은 반사광 배제 `dL_signed`) + `vision_scan.raytrace_min_range: 0.0`, `waypoint_follower.loop_rate: 200 ➔ 20`, `robot_base_frame: base_footprint` 통일, Galactic 레거시 키 제거.
  - `gesture_node.py` 손가락 접힘 히스테리시스(`<= 1.05`) 교정, `network_failover.py` 백업 전환 시 유선 NIC `.13` 삭제 + `time.monotonic()` 적용.
- **Pi 5 오디오·전원·카메라 & WebUI 오프라인·경량화·실측 텔레메트리 (`speaker_node.py`, `pi5_power_node.py`, `camera_node.py`, `web_monitor_server.py`, `yolo_overlay.js`, `static/tailwind.js`, `sync_to_robot.sh`)**:
  - `speaker_node.py`: `amixer -c UACDemoV10`(폴백 `-c 0`), 오디오 프로세스 원자적 교체, Edge-TTS `8s` 타임아웃, `finally` 종료 정리, `dobingda0.mp3` 완곡 후 `dobingda.mp3` 순차 재생(`"stop"` 시 즉시 취소).
  - `pi5_power_node.py` (`/pi5/system_state` 실측 `throttled` 비트마스크 해석 발행) & `camera_node.py` (`time.monotonic()`, `spin_once(0.0)`, 동적 IMX219 엔티티명).
  - WebUI: 외부 Tailwind CDN 제거 ➔ 로컬 `/tailwind.js`(398KB) 100% 오프라인 구동, `/video_feed` `threading.Condition` 이벤트 송출, YAML/JSON `mtime` 캐싱, `tf2` 폴백으로 SLAM 중 2D 맵 위치·라이다 표시, `/api/yolo` 사람 감지 카운트다운 HUD(`3s/30s/50s`), `IS_WEB_MASTER` 이중 WebUI 충돌 차단, 실측 Pi 5 쓰로틀링 & Jetson RAM/Swap 연동, Keepout 선 두께 `0.15m` 통일, `turn_seq` 회전 레이스 차단.
  - `scripts/sync_to_robot.sh`: Pi 5·Jetson(`docker exec` `install/` 반영 포함) 전체 운영 파일 동기화 보강 및 `docs/1_master/process_specification.md` 문서 정비.

---

## [v1.4.11] - 2026-11-24

### 🎯 핵심 릴리즈 요약
바닥 장애물 회피 시 **"살짝 틀었다가 다시 밀고 나가는" 현상**을 해결하기 위해 **`local_costmap` 비전 전용 레이어(`vision_obstacle_layer`) 분리**, **카메라 38cm 사각지대 보호(`raytrace_min_range: 0.40`)**, **MPPI 회피/인플레이션 밸런스 재조정**, 그리고 **`floor_obstacle_node.py` 회전 잔상 제거(`beam_hold_sec: 0.0`) + 좌우 `±3cm` 빔 패딩(`lateral_padding_m: 0.03`)**을 적용했습니다 *(로컬 검증 완료, Pi 5·Jetson 실기 배포는 전원 투입 후 `sync_to_robot.sh all` 대기)*.

### 🚀 세부 변경 내역
- **Nav2 `local_costmap` 비전 레이어 분리 & MPPI 회피 보강 (`src/pi5/jta_bringup/config/nav2_params.yaml`)**:
  - `local_costmap.plugins`에 `vision_obstacle_layer`(`nav2_costmap_2d::ObstacleLayer`, `combination_method: 1`)를 신설해 `/scan`(2D LiDAR)의 `clearing: True` raytrace가 바닥 장애물을 실시간 삭제하던 구조적 충돌을 차단.
  - `vision_scan.raytrace_min_range: 0.0 ➔ 0.40` 적용으로 카메라 하단 사각지대(전방 `~38cm`) 진입 시 코앞 장애물 셀이 지워지지 않고 `odom` Costmap에 유지되도록 보호.
  - MPPI `CostCritic.cost_weight: 1.5 ➔ 4.0`, `PathAlignCritic.cost_weight: 14.0 ➔ 9.0`, `local_costmap` `inflation_radius: 0.19 ➔ 0.25`, `cost_scaling_factor: 15.0 ➔ 7.0`으로 조정해 장애물 우회 여유 및 주행 안정감 강화.
- **바닥 장애물 가상 라이다 잔상 제거 & 폭 패딩 (`src/jetson/floor_obstacle_node.py`)**:
  - `beam_hold_sec: 0.8 ➔ 0.0`: 로봇 회전 시 `base_footprint` 기준 잔상이 함께 회전·번지던 현상을 제거하고 시야 이탈 장애물 유지를 `odom` 기반 `vision_obstacle_layer`에 위임.
  - `lateral_padding_m: 0.03` 파라미터 신설: `_build_scan_msg()`에서 거리 비례 각도(`atan2(0.03, dist)`)만큼 좌우 빔을 확장해 물체 모서리 긁힘 방지.

---

## [v1.4.10] - 2026-11-24

### 🎯 핵심 릴리즈 요약
**Pi 5 · Jetson 실기 스크립트/설정/마스크 1:1 백업 및 `launch_yolo11n.sh` 동기화 배포**, **Pi 5 부팅 세레머니 동적 랜덤 선곡(`~/music/song`) + 인트로 TTS(`"노래 한 곡 듣고 가시죠."`)**, 그리고 **`dobingda.mp3` Edge-TTS 4단 안내 음성 대체**를 완료했습니다.

### 🚀 세부 변경 내역
- **Pi 5 부팅 세레머니 동적 랜덤 선곡 + 인트로 TTS (`src/pi5/jta_bringup/jta_bringup/speaker_node.py`)**:
  - 기존 `~/music/neon.mp3` 단일 경로 및 폴백을 제거하고, `_select_boot_song()`으로 `~/music/song` 내 `.mp3`/`.wav` 파일을 스캔해 무작위 선곡(숨김파일·서브디렉터리 제외, 공용 헬퍼 `_build_audio_cmd()`·`_speak_tts()` 추출).
  - `_boot_ceremony_worker()` 시퀀스: `3.0초` 대기 ➔ 볼륨 `80%` ➔ *"노래 한 곡 듣고 가시죠."* Edge-TTS 발화(오프라인 폴트 시 에러 로그 후 진행) ➔ `1.0초` 대기 ➔ 랜덤 선곡 완곡 재생(`"stop"` 즉시 스킵 유지) ➔ 볼륨 `95%` 상향 ➔ *"시스템을 동작합니다."* 발화 ➔ `/dev/shm/pi5_boot_ceremony_done` 기록.
  - `scripts/sync_to_robot.sh pi5` 배포 및 `pi5_speaker_node` 재기동(`PID 25545`) · `~/music/song` 2곡 인식 확인(재부팅 실물 세레머니 송출은 현장 확인 대기).
- **사람 감지 음원 Edge-TTS 안내 음성 대체 (`Pi 5 ~/music/dobingda.mp3`)**:
  - 기존 음원을 `dobingda.mp3.bak`(`94,119B`)으로 백업하고 `ko-KR-SunHiNeural` 4단 멘트(*"사람이 감지되었습니다." × 2 ➔ "용건을 말씀해 주세요." ➔ 2.95s 무음 ➔ "앗! 죄송합니다. 제가 마이크가 없네요... 허허.."*, `100,224B`, `16.704s`)로 합성 교체(`mpg123 -t/-w` 디코딩·무음 구간 `silent_peak=0` 검증 완료, 실물 스피커 청취는 현장 확인 대기).
- **3-Tier 실기 ↔ 레포 1:1 백업 동기화 & 배포 하네스 보강 (`src/pi5/`, `src/jetson/`, `maps/`, `scripts/sync_to_robot.sh`)**:
  - Pi 5(13종): `src/pi5/scripts/` (`jta-web-monitor.service`, `inner_webui_launch.sh`, `run_robot_autostart.sh` `1469B`, `inner_robot_launch.sh`, `inner_nav2_launch.sh`, `inner_slam_launch.sh`), `src/pi5/utils/pi5_power_daemon.py`, `patrol_waypoints.yaml`(`dock`·`WP1~WP7`), `detection_zones.json`(2구역), `maps/keepout_mask.*`·`speed_mask.*` 백업.
  - Jetson(5종 + 배포): `src/jetson/scripts/` (`network_failover.py`, `network-failover.service`, `install_failover.sh`, `test_failover_status.sh`, `rotate_screen.sh`) 백업 및 `scripts/sync_to_robot.sh` `sync_jetson()`에 `launch_yolo11n.sh` 추가 후 Jetson `~/ros2_ws/`·`~/ros2_JTA/` 배포 일치 검증 완료.

---

## [v1.4.9] - 2026-11-23

### 🎯 핵심 릴리즈 요약
**전 구간 상시 바닥 장애물 감지(`/vision/scan`) + Nav2 Costmap 연동**, **ArUco 숫자 카드(`ID 0~10`) + 한 손 제스처(👍 Nav2·자동순찰 시작 / ✋ Nav2 종료) 하이브리드 제어**, **OpenCR `10.5V` 저전압 자동 복귀(`dock`) & 단절 UI(`🔴 OpenCR OFF`) 교정**, 그리고 **사람 근접 감지·추종 `1.2m(250px) / 30초 / 50초 쿨다운` 통일**을 완료했습니다.

### 🚀 세부 변경 내역
- **전 구간 상시 바닥 장애물 감지 & Costmap 연동 (`src/jetson/floor_obstacle_node.py`, `nav2_params.yaml`, `yolo_overlay.js`, `web_monitor_server.py`)**:
  - `/yolo/zone_enable`과 독립적으로 `/camera/image_raw/compressed`를 상시 구독해 OpenCV LAB 색차(`chroma_thresh=12.0`) + 명도·에지(`luma_thresh=28.0`, `edge_low=40`, `edge_high=110`, `min_area_px=335`, `roi_top_ratio=0.58`) + YOLO 하단 좌표 보너스로 바닥 물체 검출.
  - 연속 2프레임 확정(`confirm_frames=2`) 및 `0.8초` 잔상 유지(`beam_hold_sec=0.8`)를 적용해 전방 `±33°` 67빔 가상 라이다(`/vision/scan`, `BEST_EFFORT`)를 `nav2 local_costmap` `vision_scan` 레이어(`0.20~1.50m`, `raytrace 1.8m`)에 공급하고 WebUI `<canvas>`에 바닥 ROI·장애물 박스·거리 배지를 표시.
- **ArUco 숫자 카드 + 한 손 Nav2/자동순찰 제어 (`src/jetson/gesture_node.py`, `patrol_node.py`, `web_monitor_server.py`, `yolo_overlay.js`, `docs/aruco_wp_cards.pdf`)**:
  - Jetson `gesture_node.py`: OpenCV `DICT_4X4_50` ArUco 카드(`ID 0=ESTOP 0.25s`, `ID 1~10=GOTO_WP 0.8s`) + `MediaPipe Hands`(`model_complexity=1`, `mp_frame_stride=2`) 한 손 펼침(`✋`, `1.5s` ➔ `NAV2_STOP`) / 엄지척(`👍`, `2.0s` ➔ `NAV2_START`)을 `/gesture/cmd`·`/gesture/state`로 발행 (A4 인쇄용 카드 PDF 11장 포함).
  - Pi 5 `patrol_node.py` & `web_monitor_server.py`: `ESTOP` 시 Goal 취소·정지·TTS, `GOTO_WP` 시 등록 WP 1~10 단일 이동·도착 TTS, `NAV2_START` 시 Nav2 기동 ➔ `auto_localizer` 완료 대기 후 자동 순찰 ON + TTS, `NAV2_STOP` 시 대기 취소 및 Nav2 종료 + TTS, WebUI `<canvas>` 우측 상단 HUD 진행률 배지 표시.
- **OpenCR `10.5V` 저전압 자동 복귀(`dock`) & 단절 UI 교정 (`patrol_node.py`, `web_monitor_server.py`, `map.html`, `index.html`)**:
  - `patrol_node.py`: `/battery_state` `9.0V < V <= 10.5V` `5.0초` 연속 지속 시 1회 래치 후 `patrol.dock` 좌표로 Nav2 자율 복귀(`DOCK_RETURN` / `DOCKED` + TTS, `11.5V` 회복 시 래치 해제).
  - WebUI `/map`: `🔌 현재 위치 충전소 지정 (10.5V 복귀)` 버튼 및 `set_dock` API(`patrol.dock` 저장·보존) 추가.
  - WebUI 단절 UI 교정: `NO_CONNECT` 전환 시 `voltage=0.0`, `is_switch_on=False`, `torque=False` 초기화 및 `map.html`/`index.html` 상단 전압바 조건을 `status !== 'ONLINE'`으로 교정해 단절 시 즉시 `🔴 OpenCR OFF` 표시.
- **사람 근접 감지·추종, Pi 5 WebUI `systemd` & 동기화 하네스 정비 (`vlm_service_node.py`, `patrol_node.py`, `sync_to_robot.sh`, `launch_yolo11n.sh`, `src/jetson/AGENTS.md`)**:
  - 사람 근접 감지·추종 기준을 `1.2m (h >= 250px) / 3초 확정 / 30초 추종 / 50초 쿨다운`으로 양측 통일.
  - Pi 5 `/etc/systemd/system/jta-web-monitor.service` (`User=<PI_USER>`, `Restart=always`, `RestartSec=5`, `ExecStartPre`/`ExecStopPost` 포트 정리) ➔ `<PI_HOME>/inner_webui_launch.sh` (`distrobox humble` CycloneDDS + ROS 2 로드 ➔ `~/jta_web/web_monitor_server.py :9999`) 부팅 자동화 및 `0.0.0.0:9999 LISTEN` · `/api/health ALL_GREEN` 검증 반영.
  - `sync_to_robot.sh`에 `gesture_node.py`·`floor_obstacle_node.py` 및 Pi 5 `~/jta_web/` 동기화 추가, `src/jetson/scripts/launch_yolo11n.sh`에 3종 보조 노드 자동 기동 블록 반영.

---

## [v1.4.8] - 2026-11-20

### 🎯 핵심 릴리즈 요약
**WebUI 원본 JPEG 패스스루 + 브라우저 `<canvas>` YOLO 오버레이 전환 및 Pi 5(`:9999`) 로컬 이전·모바일 세로 스크롤 교정**, 그리고 **지정순찰모드(`custom_order`) · 부팅 볼륨 단계 제어(`80%→95%`) · 추적 종료 음성 안내 · 사람 감지 `1.7m (176px)` 통일**을 완료했습니다.

### 🚀 세부 변경 내역
- **WebUI 패스스루 & Pi 5 로컬 전환 (`web_monitor_server.py`, `static/yolo_overlay.js`, `static/index.html`, `static/map.html`)**:
  - `image_callback`의 매 프레임 `cv2.imdecode` ➔ OpenCV 박스 합성 ➔ `cv2.imencode(Q75)`를 제거하고 원본 JPEG(`Q90`) 패스스루(`/video_feed`, 시퀀스 기반 중복 송출 방지)로 전환.
  - 경량 JSON API(`GET /api/yolo`) 및 정적 렌더러(`/yolo_overlay.js`)를 추가해 대시보드·맵 양쪽 브라우저 `<canvas>`에서 HiDPI 비율로 바운딩 박스를 직접 렌더링.
  - `IS_PI5_LOCAL` 듀얼 모드(Pi 5 로컬 `bash`/`shutil.copy2` 직접 제어)를 추가해 Pi 5(`<PI5_IP>:9999`)에서 독립 구동 완료 (노트북 `jta-web-monitor.service` stop/disable).
  - `/map` 모바일 세로 화면 스크롤 차단(`h-screen overflow-hidden`)을 반응형(`min-h-screen lg:h-screen overflow-y-auto lg:overflow-hidden`)으로 교정.
- **순찰·추종·음성 고도화 (`patrol_node.py`, `speaker_node.py`, `vlm_service_node.py`, `patrol_waypoints.yaml`)**:
  - `speaker_node.py`: 부팅 세레머니 `neon.mp3` 재생 볼륨 `80%` 적용 후 안내 멘트 직전 운용 볼륨 `95%` 자동 상향.
  - `patrol_node.py` & WebUI: 지정순찰모드 모달 및 `custom_order` 사용자 지정 순서 순찰, 추적 종료 시 `/tts/say` `"추적을 종료합니다."` 발화, `FOLLOWING` 중 `/yolo/zone_enable=True` 유지.
  - `vlm_service_node.py` & `patrol_node.py`: 사람 근접 감지 기준을 `1.7m (h >= 176px)`로 통일.

---

## [v1.4.7] - 2026-11-19

### 🎯 핵심 릴리즈 요약
**Nav2 `behavior_server` 정정·좁은 공간 탈출 튜닝 · `my_map_261113.pgm` 노이즈 정리/확장 · 최단거리/선택 WP/3초확정 음성·20초 사람추종(40초 쿨다운, IDLE 포함)** 및 **WebUI `LOCALIZING`·배지 교정 / 전 문서 `Approved`·`26-xx-xx` 표준화**를 완료했습니다.

### 🚀 세부 변경 내역
- **Nav2 좁은 공간 탈출 튜닝 & Goal Tolerance 완화 (`src/pi5/jta_bringup/config/nav2_params.yaml`)**:
  - `recoveries_server:` → **`behavior_server:`** (`nav2_behaviors/Spin|BackUp|Wait`, `base_link`, `simulate_ahead_time: 0.5`) 정정으로 좁은 공간 복구 조기 중단(`Collision Ahead`) 해소.
  - `local_costmap` `resolution: 0.025`, `global_costmap` `resolution: 0.05`, `planner_server` `expected_planner_frequency: 2.0`, `use_astar: true`, 양쪽 Costmap `robot_radius: 0.145`, `inflation_radius: 0.19`, `cost_scaling_factor: 15.0`, `MPPI` `vx_max: 0.16`, `CostCritic.cost_weight: 1.5`, `general_goal_checker` `xy_goal_tolerance: 0.35`, `yaw_goal_tolerance: 0.55` 반영.
- **AMCL 맵 정리 및 우측 확장 (`maps/my_map_261113.pgm`)**:
  - 고립 스캔 노이즈 99개 클러스터(118px) 제거 및 우측 `+20px` 미지영역(`205`) 패딩(`148×120 ➔ 168×120`)으로 `WP4` `worldToMap failed` 해소.
- **순찰·추종·음성 통합 프로토콜 고도화 (`patrol_node.py`, `vlm_service_node.py`, `patrol_waypoints.yaml`, `web_monitor_server.py`, `map.html`, `scripts/sync_to_robot.sh`)**:
  - `/amcl_pose` 기반 최단거리 WP 시작, 선택 WP 순찰(`patrol_waypoints.yaml` `enabled` + `/api/patrol` `selected_indices`), `_internal_canceled_goals` 내부 취소 핸들 추적으로 추종 전환 시 순찰 중단 버그 수정.
  - 순찰 ON 및 일반 Nav2 대기(`IDLE`) 상태에서 탐지구역 내 `2.0m` (`bh >= 150px`) 사람 **`3초` 연속 감지 확정** 시 `dobingda.mp3` 재생 + `20초` 비주얼 서보잉 추종(`FOLLOWING:PERSON`, `/scan <= 0.65m` 정지) 동시 시작, 확정 기준 **`40초`** 재구동 차단.
- **WebUI 관제 & 전 문서 표준화 (`web_monitor_server.py`, `map.html`, `docs/` 18개 문서)**:
  - 순찰 시작 시 `wp_goal_status` 초기화 및 순찰 상태 우선 표기, `jta_auto_localizer` 구동 중 `LOCALIZING`(`위치 찾기 중...`) 배지 표시, 전 문서 버전 표 `Approved`·`26-xx-xx` 통일 및 포트폴리오 1행 1사진·2.2절 표 GitHub 호환 정돈.

---

## [v1.4.6] - 2026-11-18

### 🎯 핵심 릴리즈 요약
**WebUI `/` 기본 실시간 맵 전환 · 2-Tab/3단 레이아웃 · 대시보드 VLM 채팅창(TTS 연동)** 및 **Jetson VLM 순수 시각 우선(`640×480`) 고도화**를 완료했습니다.

### 🚀 세부 변경 내역
- **WebUI 라우팅 및 대시보드 VLM 채팅 (`src/laptop/web/web_monitor_server.py`, `static/index.html`, `static/map.html`)**:
  - `:9999/` 기본 화면을 실시간 맵(`map.html`)으로 전환하고 대시보드를 `/dashboard`로 분리.
  - `/map` 우측 패널의 `🎯 WP 이동` · `🚧 위험구간`을 2-Tab으로 정리.
  - 대시보드(`index.html`)를 3단(좌 6열 비전 · 중 3열 VLM 비전 채팅창 · 우 3열 3-Tier/OpenCR 4행)으로 재편하고, `POST /api/vlm/ask` + `/vlm/response` 구독으로 채팅 질의 ➔ Pi 5 Edge-TTS 자동 발화 연동.
- **Tier 2 Jetson VLM 인식률·역할 분리 (`src/jetson/vlm_service_node.py`)**:
  - 수동 `🗣️ VLM` 호출 시 `allow_person_alert=False` 적용(백그라운드만 1.5m 사람 경보 유지), 사람 감지 시 `"사람이 보이고, "` 안전 프리픽스 후처리, 입력 해상도 `640×480` 복구 및 영어 시각 지시 프롬프트(`max_new_tokens=40`) 반영.

---

## [v1.4.5] - 2026-11-18

### 🎯 핵심 릴리즈 요약
**MPPI 실주행 검증·Costmap 좁은 틈 우회 완화 튜닝** 및 **WebUI `:9999/map` 태블릿 관제 고도화**를 완료했습니다.

### 🚀 세부 변경 내역
- **배포 하네스 보강 (`scripts/sync_to_robot.sh`, `6e2344c`)**: `sync_pi5()`에 `nav2_params.yaml` (`src/` 및 `install/share/...`) 동기화 경로 추가.
- **Nav2 Costmap 튜닝 & 필터 스캐폴딩 (`src/pi5/jta_bringup/`)**:
  - `nav2_params.yaml`: `local_costmap` (`robot_radius: 0.19`, `inflation_radius: 0.25`, `cost_scaling_factor: 8.0`), `global_costmap` (`robot_radius: 0.19`, `inflation_radius: 0.28`, `cost_scaling_factor: 8.0`)로 좁은 틈 우회 완화 및 `KeepoutFilter`/`SpeedFilter` (`/keepout_filter_mask`, `/speed_filter_mask`) 플러그인 반영.
  - `nav2.launch.py`, `setup.py`, `blank_filter_mask.*`: Keepout/Speed 마스크 서버 연동 및 `.pgm` 설치 규칙 추가.
- **WebUI `/map` 태블릿 관제 고도화 (`src/laptop/web/web_monitor_server.py`, `static/map.html`)**:
  - `POST /api/map/hazard_zones` (`hazard_zones.json`, 최대 50개 위험구간 저장·동기화), `POST /api/nav_goal` (등록 WP 개별 1회 이동 + 확인 팝업), `POST /api/nav_cancel` (토크·AMCL 유지 브레이크).
  - `🛑 E-STOP` + `⚡ 모터 ON` 복구, `#drive-ready-badge` 주행 가능 상태 배지, 초기화 버튼 빨강/초록 피드백, 1초 맵 폴링, Teleop 패드 1.5배 확대, `🗣️ VLM` 브리핑 버튼 추가.

---

## [v1.4.4] - 2026-11-17

### 🎯 핵심 릴리즈 요약
**Nav2 로컬 컨트롤러 `MPPIController` 전환 (`nav2_params.yaml` Pi 5 경량 튜닝)**을 반영했습니다.

### 🚀 세부 변경 내역
- **MPPI 로컬 컨트롤러 전환 (`src/pi5/jta_bringup/config/nav2_params.yaml`)**:
  - `controller_server.FollowPath.plugin`을 `dwb_core::DWBLocalPlanner` ➔ `nav2_mppi_controller::MPPIController`로 전환 (기존 DWB 설정은 `# [BACKUP] DWB parameters` 주석 보존).
  - Pi 5 경량 튜닝: `motion_model: "DiffDrive"`, `batch_size: 600`, `time_steps: 25`, `model_dt: 0.1`, `vx_min: -0.10`(후진 탈출), `wz_max: 1.2`, Critics 8종(`ConstraintCritic`, `CostCritic`, `GoalCritic`, `GoalAngleCritic`, `PathAlignCritic`, `PathFollowCritic`, `PathAngleCritic`, `PreferForwardCritic`) 적용.

---

## [v1.4.3] - 2026-11-17

### 🎯 핵심 릴리즈 요약
**Jetson VLM 이벤트 우선·경량화 튜닝(`vlm_service_node.py` Option A)** 및 **3-Tier 전체 기술 문서(`docs/`) 버전·내용 전수 동기화**를 완료했습니다.

### 🚀 세부 변경 내역
- **Tier 2 VLM 추론 응답성·발열 균형 튜닝 (`src/jetson/vlm_service_node.py`)**:
  - 입력 이미지 `(448, 336)` 리사이즈(`cv2.INTER_AREA`) 및 `max_new_tokens=32` 축소로 단일 VLM 추론 부하 경감.
  - 백그라운드 자동 루프 쿨다운을 `background_cooldown_sec = 25.0`으로 완화하여 주행 중 발열 안정화.
  - `_build_situation_prompt()` 공통 헬퍼 분리 후, `/vlm/trigger` `"즉시"`(또는 빈 문자열) 수신 시 캐시 대신 현재 최신 프레임 즉시 추론(`_run_on_demand_query`)으로 전환.
- **전체 문서 체계 정합성 정비 (`docs/`, `CHANGELOG.md`)**:
  - `docs/INDEX.md`, `docs/2_tiers/tier2_jetson_orin_nano/vlm_tts_service.md`, `docs/3_daily_review/2026-11-17_*.md`, `docs/1_master/*`, `docs/4_career/*` 최신화 및 버전·검증 수치(`64/64 PASS`) 동기화.

---

## [v1.4.2] - 2026-11-17

### 🎯 핵심 릴리즈 요약
**다중 웨이포인트 자율 순찰(`patrol_node`)**, **`:9999` WebUI 실시간 2D 맵(`/map`) 3열 가두리 & OpenCR 전압 알람**, **AMCL 정밀·납치 복구**, **`speaker_node` `dmix`/`stop`**, **`foxglove-bridge.service` 자동 기동**을 반영했습니다.

### 🚀 세부 변경 내역
- **다중 웨이포인트 순찰 & 오디오 개선 (`src/pi5/jta_bringup/`)**:
  - `patrol_node.py`, `config/patrol_waypoints.yaml` 신설 및 `setup.py`·`nav2.launch.py` 통합 (`enable_patrol:=True`, `patrol_auto_start:=False`, `/patrol/set_enabled`, `/patrol/status`).
  - `speaker_node.py`: ALSA `plug:dmix:CARD=UACDemoV10,DEV=0` 전환, `_run_audio_proc`/`stop_current_audio` 기반 `/play/sound` `"stop"` 즉시 중단 지원.
- **AMCL 정밀·자동 복구 & Nav2 실기 정정 (`src/pi5/jta_bringup/config/nav2_params.yaml`, `auto_localizer.py`)**:
  - `amcl` 정밀도·복구 동시 튜닝: `recovery_alpha_fast: 0.1`, `recovery_alpha_slow: 0.001` 활성화 및 `update_min_d: 0.08`, `update_min_a: 0.05`, `sigma_hit: 0.08`, `z_hit: 0.9`, `z_rand: 0.1` (`auto_localizer.py` `cov_threshold: 0.02`).
  - `recoveries_server` `robot_base_frame: base_footprint`, `use_sim_time: False` 버그 수정 및 Pi 5 `src`/`install` 동기화.
  - DWB/Costmap 튜닝 (`BaseObstacle.scale: 0.08`, `inflation_radius: 0.30`).
- **`:9999` WebUI 2D 맵(`/map`) 3열 가두리 · OpenCR 전압 알람 & Foxglove 자동실행 (`src/laptop/web/`, 호스트 systemd)**:
  - `web_monitor_server.py`·`static/index.html`·`static/map.html`: OpenCR `10.8V` 즉시종료/`11.0V` 저전압 주의 알람 및 1P·2P 상단 헤더 1행 `#header-voltage-bar` 연동.
  - `static/map.html`: 좌측 `260px` 스몰 고정 패널(4행 범례·`#nav-mode-badge`·`#patrol-status-badge`·카메라·YOLO `/video_feed`) + 중앙 탐색영역 정중앙 `fitMap`(`jta_map_view_v4`) + 우측 제어 사이드바.
  - `~/.config/systemd/user/foxglove-bridge.service` 등록 (`ros2_humble_basic` 내 `foxglove_bridge` `:8765` 상시 자동 실행).

---

## [v1.4.1] - 2026-11-16

### 🎯 핵심 릴리즈 요약
실주행 테스트 기반 **Nav2 AMCL/Costmap 정밀 튜닝**과 **`:9999` WebUI 긴급 제어·전체 초기화(`Full Reset`)·UX 레이아웃 개편**을 반영했습니다.

### 🚀 세부 변경 내역
- **Nav2 / AMCL 실주행 튜닝 (`nav2.launch.py`, `nav2_params.yaml`, `auto_localizer.py`)**:
  - `auto_spin: True` 복구, AMCL 전역 수렴 강화(`max_particles: 5000`, `max_beams: 180`, `nomotion` x3 선행, `20s` 회전), `stop_robot()` DDS 플러시 대기(`0.25s`) 보강.
  - `local_costmap` 과포화(89%) 해소 및 회피 반경 균형 튜닝(`obstacle_layer` 단일화, `inflation_radius: 0.35/0.40`, `cost_scaling_factor: 5.0`, `BaseObstacle.scale: 0.25`).
- **`:9999` WebUI 안전·UX 개편 (`web_monitor_server.py`, `static/index.html`)**:
  - `watchdog_callback` `throttled` 상태 복구 버그 수정.
  - `nav2_stop`, `slam_stop`, `relocalize`(🎯 위치 찾기), `full_reset`(🔄 주행 전체 초기화: OpenCR 정지 + Pi 5 정리 + `foxglove_bridge` 재기동) 무비밀번호 즉시 실행 적용.
  - Jetson 카드 최상단 이동, 제어 패널 내 **🔒 모터 락** 버튼 통합, 영상 하단 갱신 주기 바 이동 및 **초기값 `1초`** 설정.

---

## [v1.4.0] - 2026-11-16

### 🎯 핵심 릴리즈 요약
3-Tier 분산 DDS를 **CycloneDDS Unicast Peer**로 전면 전환하여 중앙 Discovery Server 단일 장애점(SPOF)을 제거하고, `jta_bringup` Nav2 통합, **Lichtblick + `foxglove_bridge` 2D/3D 관제**, 그리고 **`:9999` WebUI PIN 인증 기반 Nav2/SLAM 원격 제어**를 구축했습니다.

### 🚀 세부 변경 내역
- **CycloneDDS Unicast Peer 전환 (`config/cyclonedds.xml`)**: 3-Tier(`<LAPTOP_IP>`, `<JETSON_IP>`, `<PI5_IP>`) 및 `<LAN_SUBNET>` 서브넷 인터페이스 고정으로 Jetson APIPA 격리 해결, 15 FPS 비전·YOLO 통신 실증.
- **`jta_bringup` Nav2 통합**: `nav2.launch.py`, `config/nav2_params.yaml`, `auto_localizer.py`(Global Localization + `/cmd_vel` 5회 정지 가드레일) 추가.
- **관제 및 WebUI 고도화**:
  - `Lichtblick v1.30.0` + `foxglove_bridge(:8765)` 전용 레이아웃(`config/lichtblick_nav2_slam.json`, `map` 기준 고정).
  - `:9999` WebUI 영상 하단에 **Nav2 / SLAM 시작·종료(PIN 인증 & 상호 인터락)** 패널 및 실시간 모드 배지(`NAV2` / `SLAM` / `IDLE`) 추가.

---

## [v1.3.0] - 2026-11-05

### 🎯 핵심 릴리즈 요약
스마트폰 브라우저(iOS 사파리 / 안드로이드 크롬)로 로봇 현장 텔레메트리와 AI 비전 피드를 즉시 관제할 수 있는 **스마트폰 연동 3-Tier 모바일 WebUI 관제 서버 (포트 :9999)**를 신규 구축했습니다. 별도의 앱 설치 없이 웹 표준(MJPEG + JSON API)을 활용하여 초저지연 비전 미러링과 사용자 맞춤형 주기 제어가 가능한 헬스체크 제어바를 완비했습니다.

### 🚀 세부 변경 내역

#### 1. Tier 3: 관제 노트북 (Web Server & Dashboard)
- **모바일 WebUI 백엔드 서버 구축 (`src/laptop/web/mobile_web_server.py`)**:
  - 파이썬 표준 라이브러리 `ThreadingHTTPServer` 기반 초경량·멀티스레드 무의존성 웹서버 탑재.
  - ROS 2 FastDDS Domain 30 토픽 실시간 구독 및 캐싱:
    - Pi 5 카메라 압축 영상 (`/camera/image_raw/compressed`) + 젯슨 YOLO JSON (`/yolo/detections`) 실시간 OpenCV 오버레이 합성 후 MJPEG (`/video_feed`) 15~20 FPS 송출.
    - Pi 5 PMIC 전압/온도 (`/pi5/battery_state`), OpenCR 18650 전압 (`/battery_state`), 라이다 5Hz 주기 (`/scan`) 통합 `/api/health` JSON 제공.
  - Mint 호스트 실행 시 Docker(`ros2_humble_basic`) 자동 감지 및 컨테이너 내부 재호출 래퍼 탑재.
- **모바일 최적화 프론트엔드 UI (`src/laptop/web/static/index.html`)**:
  - Tailwind CSS 기반 모바일 반응형 다크 테마 카드 레이아웃.
  - **헬스체크 제어바 탑재**: `[즉시 갱신]` 버튼 + `[1초 / 3초(기본) / 5초 / 수동]` 주기 선택 토글 제공으로 불필요한 깜빡임 방지 및 폰 배터리 절약.
  - OpenCR 18650 배터리 스위치 OFF 감지 시 실시간 경고 뱃지 표출.
- **원클릭 실행 스크립트 신설**:
  - `scripts/run_mobile_web.sh` (기본 포트 9999 자동 바인딩).

---

## [v1.1.0] - 2026-10-29

### 🎯 핵심 릴리즈 요약
라즈베리파이 5 CSI 카메라의 2배 줌인 크롭 문제를 해결하여 100% 풀 화각(Full FOV)을 확보하였으며, 젯슨 오린 나노의 Ampere GPU 연산력에 맞춰 YOLOv8 Small 및 640 해상도 업그레이드를 완료했습니다. 또한 무선 Wi-Fi 대역폭 한계로 인한 화면 끊김을 제거하기 위해 초저지연 압축 스트림 파이프라인과 타임스탬프 기반 양방향 스마트 싱크 하네스를 구축했습니다.

### 🚀 세부 변경 내역

#### 1. Tier 1: Raspberry Pi 5 & OpenCR
- **카메라 2배 줌인(Crop) 해결 및 광각 풀 화각 복원**:
  - IMX219 센서 드라이버를 `1640x1232` (2x2 Binning, 100% Full FOV 모드)로 전환 (`crop: 3280x2464` 전체 시야각 확보).
  - OpenCV BGR 변환 후 `640x480`으로 고속 리사이즈하여 기존 ROS 2 토픽 계약 및 대역폭 유지.
  - 파일: [`src/pi5/camera_ros2_stream_optimized.py`](src/pi5/camera_ros2_stream_optimized.py)

#### 2. Tier 2: Jetson Orin Nano
- **YOLOv8 모델 및 해상도 최적화**:
  - 기존 극소형 `yolov8n.pt` (imgsz=320)에서 **`yolov8s.pt` (imgsz=640, conf=0.35)**로 전면 업그레이드하여 실내 사물 인식률 대폭 향상 (의자, 컵, 모니터 등 80%+ 신뢰도 달성).
- **무선 관제용 초저지연 압축 스트림 토픽 추가**:
  - `/yolo/image_annotated/compressed` (`sensor_msgs/msg/CompressedImage`, JPEG Quality 75) 토픽 추가 발행 (프레임당 용량 1MB ➔ 35KB로 1/30 감소).
  - 파일: [`src/jetson/jetson_yolo_node.py`](src/jetson/jetson_yolo_node.py)

#### 3. Tier 3: Control Laptop (노트북 관제)
- **자동 디컴프레서 및 RViz2 연동**:
  - `ros2basic.sh`에 `image_transport republish` 백그라운드 자동 구동 옵션 추가.
  - RViz2 설정([`config/laptop/rviz2.conf.rviz`](config/laptop/rviz2.conf.rviz))의 Image 디스플레이를 로컬 디컴프레서 토픽(`/yolo/decompressed`)으로 연결하여 렉 없는 30 FPS 실시간 렌더링 지원.

#### 4. 하네스 엔지니어링 (Harness & Tooling)
- **양방향 스마트 싱크 스크립트 신설**:
  - [`scripts/smart_sync.sh`](scripts/smart_sync.sh): 파일의 최종 수정 시간(mtime)을 1초 단위로 자동 비교하여 최신 수정본을 양방향(노트북 ↔ 젯슨 ↔ 파이)으로 자동 싱크. `--watch` 실시간 자동 감시 모드 지원.
  - [`scripts/sync_from_robot.sh`](scripts/sync_from_robot.sh): 로봇 기기에서 수정한 코드를 로컬로 원클릭 풀(Pull)하는 도구.
- **에이전트 헌법 및 스킬 규약**:
  - 최상위 규약([`AGENTS.md`](AGENTS.md))에 압축 토픽 인터페이스 계약 공식 반영.

---

## [Unreleased / Roadmap] - 차기 개발 계획

### 🎯 [v1.2.0 계획] - SLAM / Nav2 정밀 자율주행 및 온디바이스 에지 안정화
- **Tier 1 (RPi 5)**: 작업장 현장 카메라 렌즈 수동 초점(Focus) 튜닝 및 조명 맞춤형 노출 최적화.
- **Tier 2 (Jetson Orin Nano)**: LDS-01 2D 라이다 기반 SLAM 지도 작성 및 Nav2 2~5cm 정밀 자율주행 통합. 8GB 메모리 내에서 YOLO + SLAM 동시 구동 안정성 확보.

### 🎯 [v2.0.0 최종 목표] - 차세대 멀티모달 AI 에이전틱 로봇 (YOLO + VLM + LLM)
- **Tier 2 (Jetson)**: 온디바이스 초경량 VLM(Moondream2 / Qwen2-VL 2B) 탑재 ➔ 목적지 도착 후 이벤트 트리거 1초 정밀 사물/상황 분석.
- **Tier 3 (노트북 RTX 4070)**: 로컬 고지능 LLM(Ollama: Qwen2.5 14B / Llama 3.1 8B) 탑재 ➔ Whisper 음성 인식(STT) 및 고음질 한국어 음성(TTS) HMI 연동.
- 사람의 자연어 음성 명령("회의실 테이블 위 파란 컵 가져와") 기반 완전 자율 심부름 미션 완수.

---

## [v1.0.0] - 2026-10-27

### 🎯 최초 릴리즈 요약
- 3-Tier 분산 시스템(RPi5 + Jetson Orin Nano + Control Laptop) 기본 환경 구축 완료.
- FastDDS 유니캐스트(`config/fastdds_unicast.xml`) 통신 토폴로지 제정.
- 종합 기술 마스터 아키텍처 스펙 문서([`docs/spec/robot_system_master_specification.md`](docs/spec/robot_system_master_specification.md)) 최초 제정.

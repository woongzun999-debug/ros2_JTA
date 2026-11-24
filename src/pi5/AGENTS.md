# 🍓 Tier 1: Raspberry Pi 5 & OpenCR Rules (`src/pi5/AGENTS.md`)

- **점검일**: 2026-11-24

`src/pi5/` 코드는 **Raspberry Pi 5 (`<PI5_IP>`, 계정 `<PI_USER>`)**에서 실행되며 CSI 카메라(IMX219), LDS-01 LiDAR, OpenCR(`/dev/ttyACM0`), USB 오디오를 제어합니다.

---

## 1. 하드웨어 & 저전력 보호 규칙
* **전력/CPU 보호**: 배터리 구동 시 전압 강하(Brownout) 방지를 위해 무거운 Python 루프나 SW 영상 회전/색보정을 피한다.
* **하드웨어 가속 유지**: 카메라 상하좌우 반전은 V4L2 레지스터(`horizontal_flip`, `vertical_flip`, `CPU 부하 0%`)로 처리하고, 화이트밸런스는 `cv2.LUT` 방식을 유지한다.

## 2. RP1-CFE CSI 카메라 파이프라인 (`jta_camera`)
* `libcamera` 대신 `media-ctl` + `v4l2-ctl` 직접 파이프라인을 사용한다 (`640x480`, `SBGGR8_1X8` / `BA81`).
* `/dev/media*`, `/dev/v4l-subdev*` 및 IMX219 엔티티명은 재부팅·포트 변경 시 변할 수 있으므로 `get_media_device()`, `get_imx219_info()`/`get_imx219_subdev()` 동적 탐색을 유지한다.
* FPS 페이싱은 NTP 역행 프리징 방지를 위해 `time.monotonic()`을 사용하고, 루프 내 `rclpy.spin_once(self, timeout_sec=0.0)`로 파라미터 서비스를 유지한다.
* 비압축 `/camera/image_raw`는 구독자가 있을 때만 On-Demand 발행하여 CPU/열 부하를 억제한다.

## 3. 모터 제어(`/cmd_vel`) 및 안전 규약
* **종료 시 모터 정지 필수**: `/cmd_vel` 발행 노드(`patrol_node`, `auto_localizer`, `obstacle_avoidance`)는 `SignalHandlerOptions.NO` + `SIGINT`/`SIGTERM` 핸들링 및 `finally`에서 `linear.x = 0.0`, `angular.z = 0.0`을 **최소 5회 연속 발행(`stop_robot()`)**한다.
* **비상 정지 보존**: `obstacle_avoidance.py`의 `EMERGENCY_STOP_DIST = 0.18`m, `utils/stop.sh`(CycloneDDS `ROS_DOMAIN_ID=30` + `/motor_power false` + 순찰/Nav2 취소 + `/cmd_vel=0`), WebUI 원자적 `/api/estop`을 약화시키지 않는다.
* **LiDAR QoS**: `/scan` 구독 시 반드시 `ReliabilityPolicy.BEST_EFFORT`를 사용하며, 정면 장애물 각도는 `atan2(sin, cos)`로 좌우 대칭(`±25°`) 정규화한다.

## 4. 컨테이너 실행 경계
* **호스트 OS**: `jta_camera`, `utils/check_power.py`, `utils/pi5_power_daemon.py` (`vcgencmd`, `media-ctl` 직접 접근), `scripts/run_robot_autostart.sh`(중복 실행 검사 최우선), `scripts/inner_*_launch.sh`, `scripts/jta-web-monitor.service`
* **Distrobox (`humble`)**: `turtlebot3_bringup`, `jta_bringup` (`robot.launch.py`, `nav2.launch.py`, `auto_localizer.py`, `patrol_node.py`, `speaker_node`, `pi5_power_node` (`/pi5/battery_state` + `/pi5/system_state` 실측 쓰로틀링)), `obstacle_avoidance.py`, `lidar_manager.py`, `utils/stop.sh`

## 5. USB 오디오 & 사운드/볼륨 규약 (`speaker_node.py`)
* **인터페이스 / 엔진**: UACDemoV1.0 USB DAC (`plug:dmix:CARD=UACDemoV10,DEV=0`), Edge-TTS (`ko-KR-SunHiNeural`, `8s` 타임아웃) + `mpg123`/`aplay`, 2-Tier 정제기 `clean_speech_text`, 원자적 프로세스 교체(`proc_lock`) 및 `/play/sound` `"stop"` 즉시 중단(`stop_current_audio`) 유지.
* **토픽 계약**: `/play/sound`(별칭 `dobingda`: `dobingda0.mp3` 완곡 후 `dobingda.mp3` 순차 재생·`"stop"` 시 즉시 취소 / `bingdari` / `neon`), `/speaker/volume_set` (`amixer -c UACDemoV10 sset PCM {vol}%`, 폴백 `-c 0`), `/speaker/volume_state`, `/vlm/response`, `/tts/say`.
* **부팅 세레머니**: `/dev/shm/pi5_boot_ceremony_done` 부재 시 `~/music/song` 디렉터리의 `.mp3`/`.wav` 중 1곡을 무작위 선곡(폴백 없음)해 볼륨 **`80%`**로 *"노래 한 곡 듣고 가시죠."* 인트로 TTS ➔ `1.0초` 대기 ➔ 완곡 동기 블로킹 재생(중간 외부 사운드 Drop, `"stop"` 즉시 스킵) 후 운용 볼륨 **`95%`**로 상향하여 *"시스템을 동작합니다."* 발화로 기동 인계.

## 6. 순찰 · 제스처 · 저전압 복귀 규약 (`patrol_node.py`, `nav2_params.yaml`)
* **가상 라이다 연동 & Humble 규격**: `local_costmap`에 `vision_obstacle_layer` (`combination_method: 1`, `vision_scan`: `/vision/scan`, `raytrace 0.0~1.8m`, `obstacle 0.20~1.50m`)를 `obstacle_layer`(`/scan`)와 분리 결합하고, `waypoint_follower.loop_rate: 20` 및 전 컴포넌트 `robot_base_frame: base_footprint`를 유지한다.
* **ArUco 카드 이동 & 비상정지**: `/gesture/cmd` 수신 시 `ESTOP`(Goal 취소·정지·TTS) 및 `GOTO_WP`(등록 WP 1~10 단일 이동·도착 TTS)를 `goal_seq` 기반 Race-Free 상태 머신으로 실행한다.
* **사람 근접 추종**: `1.2m(bh >= 250px, conf >= 0.50)` `3.0초` 연속 감지 확정 시 `30초` 추종 및 확정 시점 기준 `50초` 쿨다운을 유지하며, 추적 종료 시 `/play/sound` `"stop"` 발행으로 재생 중 음원을 즉시 중단한다.
* **OpenCR `10.5V` 저전압 복귀**: `/battery_state`가 `9.0V < V <= 10.5V` 구간에서 `5.0초` 연속 지속 시 1회 래치(`low_volt_returning`, 복귀 중 사람 추종 탈선 차단) 후 `patrol.dock` 좌표로 자율 복귀(`DOCK_RETURN`/`DOCKED`, `11.5V` 회복 시 래치 해제)한다.

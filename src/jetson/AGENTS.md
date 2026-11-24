# 🤖 Tier 2: Jetson Orin Nano AI Brain Rules (`src/jetson/AGENTS.md`)

- **점검일**: 2026-11-24

`src/jetson/` 코드는 **Jetson Orin Nano (`<JETSON_IP>:2222`, 계정 `<JETSON_USER>`)**의 GPU Docker 컨테이너(`my_ros2_robot:humble`, 호스트 `~/jetson_ros2_humble` ↔ 컨테이너 `/root/ros2_ws/jetson_ros2_humble`)에서 실행됩니다.

---

## 1. 실시간 AI 추론 지연(Zero-Lag) 방지 규칙
* **프레임 드롭 가드 필수**: 콜백 내부에서 이전 프레임 추론 중일 때(`if self.processing: return`) 신규 프레임을 즉시 드롭하여 큐 적체를 막는다.
* **큐 깊이 최소화**: 영상 구독(`/camera/image_raw*`) 및 시각화 발행(`/yolo/image_annotated*`)의 QoS `depth`는 항상 `1`을 유지한다.
* **NumPy 직접 변환**: `cv_bridge` 충돌·오버헤드 방지를 위해 `np.frombuffer(..., dtype=np.uint8).reshape(...)` 직접 변환을 유지한다.

## 2. GPU(CUDA) 및 `yolo11n` 패키지 추론 규약
* 디바이스 선택은 `device = '0' if torch.cuda.is_available() else 'cpu'` 폴백 구조를 유지한다.
* 실기 표준은 `yolo11s.pt` (`imgsz=640`, `conf=0.60`, `yolo11n.launch.py`·`detector_node.py` 기본값 일치)이며, 초경량 운용 시 런치 인자로 `yolo11n.pt` (`imgsz=480`) 전환을 지원한다. `detector_node.py`는 2초 주기로 `/jetson/system_state` 실측 RAM/Swap/온도 JSON을 발행한다.

## 3. 온디바이스 VLM 표준 (`Qwen/Qwen2-VL-2B-Instruct`)
* **표준 모델**: `Qwen/Qwen2-VL-2B-Instruct` 4-bit NF4(BitsAndBytes) 상주 노드를 사용하되 **순수 On-Demand 모드(`background_loop_enabled=False`)**로 운용하여 백그라운드 25초 공회전 발열·YOLO 간섭을 차단한다.
* **지연·부하 최적화**: `camera_callback`은 raw JPEG 바이트만 보관하고 추론 직전 락 밖에서 1회 디코딩하며, non-blocking 락으로 연타 스레드 누적을 방지한다.
* **환각 금지**: 시스템 프롬프트에서 화면에 명확히 식별 가능한 사물만 팩트 기반으로 서술하고 추측·과장을 금지한다.
* **영구 배제 모델**: **`Moondream2 (vikhyatk/moondream2)`**는 `my_ros2_robot:humble` 라이브러리 충돌 및 환각 문제로 코드·설계에 절대 포함하지 않는다.

## 4. YOLO 사람 근접 트리거 & VLM 수동 브리핑
* **근접 감지 확정 트리거**: 무거운 VLM 루프와 분리하여 YOLO 콜백에서 사람 바운딩 박스 세로 높이 $h \ge 250\text{px}$ (`conf >= 0.50`, 약 1.2m 근접)가 **`3.0초` 이상 연속 감지**(`time.monotonic()`) 확정되면 즉시 `/play/sound` (`dobingda.mp3`, Pi 5 Edge-TTS 안내 음성 + 완료 1초 후 `dobingda.mp3.bak` 순차 재생)를 발행한다 (확정 시점 기준 쿨다운 `50.0초`, Pi 5 30초 추종과 동시 시작).
* **VLM 수동 브리핑**: 정기 자동 발화는 비활성화하고 WebUI `/vlm/trigger` 수신 시 현재 프레임 즉시 브리핑(`/vlm/response` ➔ TTS 발화)을 수행한다.
* **CLI(`qss`) 조용한 채팅 분리**: 터미널 CLI 질의는 `/vlm/chat_query` ↔ `/vlm/chat_response` 전용 토픽으로 처리하여 Pi 5 스피커 발화를 유발하지 않는다.

## 5. 전력 관리 및 과전류(Over-Current) 방지
* **45W 단독 전원 분리 & `MAXN_SUPER (Mode 2)`**: 젯슨은 반드시 **45W 정품 단독 어댑터로 전원을 분리**하여 `MAXN_SUPER (Mode 2)`로 운영한다.
* **금지**: Pi 5·노트북과 멀티 어댑터를 공유하면 전압 강하로 PMIC 과전류 쓰로틀링(`CPU 115MHz` 락다운)이 발생하므로 절대 금지한다.
* `update-manager` 등 불필요한 백그라운드 데몬은 사전 종료한다.
* **네트워크 페일오버 & 통합 런처 (`scripts/`)**: `network-failover.service`(`network_failover.py`: 백업 전환 시 유선 NIC IP 삭제 + `time.monotonic()`, `install_failover.sh`, `test_failover_status.sh`)로 유선(`enP8p1s0`)↔무선(`wlP1p1s0`) 고정 IP `<JETSON_IP>/24` 자동 전환, `rotate_screen.sh` 및 `launch_yolo11n.sh` (`sync_to_robot.sh` 배포 동기화)를 유지한다.

## 6. 상시 바닥 장애물 감지 & ArUco·한손 제어 (`floor_obstacle_node.py`, `gesture_node.py`)
* **상시 바닥 장애물 (`floor_obstacle_node.py`)**: `/yolo/zone_enable`과 무관하게 상시 가동하며, OpenCV LAB/명도·에지(`roi_top_ratio=0.58`, `chroma_thresh=18.0`, `luma_thresh=38.0`, `min_area_px=650`, `confirm_frames=4`, `beam_hold_sec=0.0`, `lateral_padding_m=0.03`) + YOLO 보너스로 `/vision/scan` (`BEST_EFFORT`) 및 `/vision/floor_obstacles`를 발행한다 (`self.processing` 가드 필수).
* **ArUco + 한손 Nav2 제어 (`gesture_node.py`)**: `DICT_4X4_50` ArUco 카드(`ID 0=ESTOP 0.25s`, `ID 1~10=GOTO_WP 0.8s`) + `MediaPipe Hands`(`model_complexity=1`, `mp_frame_stride=2`, 손가락 펴짐 `>1.15` / 접힘 `<=1.05` 히스테리시스) 한 손 펼침(`✋`, `1.5s`) `NAV2_STOP` / 엄지척(`👍`, `2.0s`) `NAV2_START`를 `/gesture/cmd`·`/gesture/state`로 발행한다 (`self.processing` 가드 필수).

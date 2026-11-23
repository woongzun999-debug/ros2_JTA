# 🗺️ ros2_JTA v2.0 아키텍처 전환 및 고도화 로드맵 (Future Roadmap)

- **작성자**: `ROS2_JW` | **점검일**: 2026-11-23

> **문서 개요**: 본 문서는 `ros2_JTA` (3-Tier 분산 자율주행 및 객체인식 로봇 시스템)의 차기 고도화 아키텍처(v2.0 Target), 주행 환경 특화 안전 설계, 네트워크 트래픽 다이어트 및 무인 자율 순찰 미션 시나리오를 정의하는 마스터 기획·설계 명세서입니다.

### 📋 문서 버전 관리 (Document Version Control)
| 문서&nbsp;버전 | 개정&nbsp;일자 | 작성자 | 문서&nbsp;상태 | 변경 및 개정 내역 |
| :---: | :---: | :---: | :---: | :--- |
| `v1.0.0` | 26.11.10 | ROS2_JW | Approved | • v2.0 독립 순찰, 경사로 Keep-out Zone, 컴퓨터실 Thin Obstacle 회피 및 원클릭 SLAM 로드맵 제정 |
| `v1.4.1` | 26.11.16 | ROS2_JW | Approved | • Phase 3(CycloneDDS Unicast Peer 전환 및 WebUI Nav2/SLAM 인터락 제어) 구현 완료 상태 반영 |
| `v1.4.2` | 26.11.17 | ROS2_JW | Approved | • 다중 웨이포인트 순찰(`patrol_node`), WebUI 2D 맵(`/map`)<br>• AMCL 납치 복구(`recovery_alpha`) 및 `foxglove-bridge.service` 자동 기동 반영 |
| `v1.4.3` | 26.11.17 | ROS2_JW | Approved | • `vlm_service_node.py` Option A(이벤트 우선 + `(448, 336)` 리사이즈·`max_new_tokens=32`·25s 쿨다운) 반영 및 전체 문서 동기화 |
| `v1.4.4` | 26.11.17 | ROS2_JW | Approved | • Nav2 로컬 컨트롤러 `MPPIController` 전환 (`nav2_params.yaml` Pi 5 경량 튜닝) 반영 |
| `v1.4.5` | 26.11.18 | ROS2_JW | Approved | • OpenCR `11.0V` 5초 지속 시 지정 충전소 자동 복귀(Section 8) 차기 로드맵 등재 |
| **`v1.4.9`** | 26.11.23 | **ROS2_JW** | **Approved** | • Section 8 OpenCR 저전압 자동 복귀(`9.0V < V <= 10.5V` 5초 지속 ➔ `dock`) 및 WebUI `🔌 현재 위치 충전소 지정` 구현 완료 반영 |

---

## 1. 3-Tier 하드웨어 역할 재정의 (독립 순찰 & 전력/트래픽 다이어트)

### 1.1 Tier 1 (Mobile AMR - Raspberry Pi 5 + OpenCR 1.0)
* **전원 완전 분리**:
  * 18650 모터 배터리와 물리적으로 분리된 **대용량 PD 보조배터리(모루이 MT-45 45W 20,000mAh + Spotpear 27W 트리거 모듈)**로 Pi 5 및 현장용 USB 미니 스피커 구동 (모터 기동 전압강하로 인한 Pi 5 셧다운 원천 차단).
* **18650 모터 배터리 저전압 안전 차단 (BMS 방전 방지)**:
  * OpenCR `/battery_state` 전압을 상시 감시하여 `9.0V < V <= 10.5V` `5초` 지속 시 `patrol_node`가 지정 충전소(`dock`)로 자동 복귀(`v1.4.9` 구현 완료, `10.2V` 이하 E-Stop 방전 보호 설계 포함).
* **로컬 폐쇄 루프 자율주행**:
  * **`slam_toolbox` (2D 매핑) + `Nav2` 자율주행**을 Pi 5 로컬에서 직접 구동.
  * Wi-Fi가 일시 단절되어도 로봇 단독으로 안전하게 장애물 회피 및 비상정지 수행.

### 1.2 Tier 2 (Edge Brain - Jetson Orin Nano, 220V 상시 전원)
* **순수 비전 추론 다이어트**:
  * **YOLO11n/m** 초고속 추론 (~38ms).
  * 기존의 영상 바운딩 박스 렌더링(`plot()`) 및 JPEG 재인코딩(`cv2.imencode`)을 전면 배제하여 CPU 부하와 오버커런트 피크를 차단하고, **정밀 좌표 JSON(`[cls, conf, box]`)만 0.1ms로 경량 발행 (`/yolo/detections`)**.
* **온디바이스 VLM 양자화(Quantization) 기반 VRAM 다이어트 (2026-11-10 실증 완료)**:
  * **목적**: Nav2 코스트맵, 3D 포인트클라우드 장애물 매핑 등 추가 ROS 2 노드를 동시에 띄우기 위한 **물리적 VRAM 3.5GB+ 여유 확보**.
  * **실측 팩트 분석 (FP16 원본 vs 4-bit NF4 실측)**:
    * 모델 순수 VRAM 점유: 5.0GB ➔ **1.42GB** (**3.58GB 순수 VRAM 가용량 즉시 회수, 71.6% 절감**)
    * 모델 기동 시간: **12.21초** 1회 상주(Resident) 로딩 완료 후 상시 대기.
    * 단일 질의 추론 속도: 메모리 대역폭 병목 해소로 **10.80초** (기존 12~13초 대비 향상).
    * 한국어 상황 브리핑 및 환각 방지 가드레일 실증 확인 완료 (연구실/화이트보드/의자/도구 정밀 식별).
  * **운영 상태**: `my_ros2_robot:humble` 컨테이너 내 `vlm_service_node.py`에 BitsAndBytes NF4 탑재 완료, YOLO11n(PID 144)과 VLM 4-bit(PID 630) 충돌 없는 평화적 동시 상주 가동 중.
* **대뇌 및 양방향 음성 AI 집중**:
  * 배터리 제약이 없는 220V 상시 전원을 활용하여 **Whisper STT (음성인식) + 온디바이스 VLM/LLM (순찰 판단·접견 대화) + Piper TTS (한국어 고속 음성합성)**를 전담.

### 1.3 Tier 3 (Field Control - Control Laptop)
* **저전력·무소음(iGPU) 관제**:
  * 외장 GPU(RTX 4070)를 배제하고 **내장 그래픽(iGPU)**만 사용하여 발열과 팬 소음 제로화 (`ros2basic_nonNV.sh`).
* **원격 음성 인터콤**:
  * 작업실 책상에서 노트북 내장 마이크로 음성 명령 수신(로봇 모터/라이다 소음 0% 격리) 및 노트북 스피커로 동시 브리핑 수신.
* **노트북 의존도 제로 (Zero-Dependency)**:
  * 노트북은 순수 모니터링/인터콤용이며, **노트북을 덮거나 전원을 꺼도 로봇(Pi 5)과 젯슨 2대 기기만으로 24시간 독립 자율 순찰 수행**.

---

## 2. 주행 환경 특성 및 낙상 방지(음의 장애물) 안전 설계

### 2.1 주행 구역 환경 정의
* **메인 순찰 구역**: 20평(약 66㎡, 8m×8m) 개방형 컴퓨터실 (음영 구역 0%, 책상/본체 고정 랜드마크).
* **인접 주차/작업실**: 2m×2m 인접 작업실 (제자리 회전 도킹/주차 구역).

### 2.2 경사로(언덕) 낙상 위험 구간 방어
* **위험 요소**: 컴퓨터실과 작업실 경계의 완만한 경사로 옆 단차(낭떠러지)는 2D LiDAR가 허공을 쏴서 빈 공간으로 오인하는 '음의 장애물' 구간임.
* **3중 안전 방어 대책**:
  1. 최초 SLAM 매핑 시 조이스틱 수동 조종(Teleop)으로 안전거리 확보.
  2. 1층에서 스캔한 단차 측면 절대좌표를 기반으로 지도 위에 **`Nav2 Keep-out Zone` (가상 가드레일, 두께 10cm)**을 설정하여 경사로 정중앙 주행만 허용.
  3. OpenCR 6축 IMU 피치(기울기) 각도를 실시간 감시하여 경사로 진입 시 자동 감속(Slow-down) 제어.

---

## 3. 네트워크 트래픽 다이어트 (100Mbps급 공유기 완벽 대응)

* **단일 영상 스트림 원칙**:
  * Pi 5 ➔ 젯슨/노트북으로 압축 영상(`Quality 90`, 640x480) 1회만 발행 (~3.5 Mbps)하고, 젯슨의 역방향 영상 재전송을 폐지.
* **초경량 총합 대역폭 유지**:
  * 영상(3.5 Mbps) + 젯슨 좌표 JSON(0.1 Mbps) + 양방향 음성/텍스트(0.1 Mbps) = **시스템 전체 트래픽 4~5 Mbps 이내 유지** (100M 대역폭의 5% 미만).

---

## 4. 핵심 자율 순찰 미션 시나리오

1. **20평 컴퓨터실 무한 자율 순찰**: Nav2 웨이포인트 4개 코스를 주기적으로 순회.
2. **YOLO11 인체 감지(`person`) 경보**: 출입문/통로에서 사람 감지 시 즉시 정지 및 경보 토픽 발행.
3. **실시간 양방향 음성 인터콤 & AI 접견 (멀티룸 동시 사운드)**:
   * 젯슨 Piper TTS 음성이 로봇 USB 스피커(현장)와 작업실 노트북 스피커에서 동시에 출력.
   * 작업실 노트북 마이크로 발화 시 로봇 현장 스피커로 즉시 송출되는 원격 인터콤 통화 지원.

---

## 5. 컴퓨터실 실전 자율주행 특화: 의자/책상 다리(Thin Obstacle) 인식 & 회피 로드맵

* **환경 특성**: 20평 컴퓨터실 내 다수의 3cm 원통형 금속 의자 다리, 책상 사각 프레임, 바닥 전선 등 2D LiDAR 빔 누락 및 YOLO 기본 모델 사각지대 존재.
* **3단계 추진 계획 (Sim2Real + VLM Auto-Labeling + Costmap Fusion)**:
  1. **합성 데이터 & 무인 자동 라벨링 (Tier 3 노트북 RTX 활용)**:
     * 현장 카메라 캡처 샘플(5~10장) 기반 다양한 각도, 조명, 반사, 굵기(1~5cm) 합성 이미지 생성 및 VLM(Qwen2-VL) 그라운딩을 통한 100% 무인 Bounding Box 라벨링(`.txt`).
     * `chair_leg` 및 `desk_frame` 전용 클래스로 YOLO11s/m 고속 파인튜닝 후 Jetson 배포.
  2. **LiDAR 3cm 빔 누락 보정 (Costmap Persistence & Inflation)**:
     * 50cm 이내에서 단 1~2발이라도 스친 얇은 다리 레이저 포인트를 지도에 즉시 기억(Persistence)하고 지름 15~20cm 원형 위험구역(Inflation Radius)으로 확장하여 물리적 끼임 원천 방지.
  3. **비전-라이다 융합 회피 (Vision-LiDAR Fusion Avoidance)**:
     * Jetson YOLO의 다리 검출 방향과 라이다 단거리 스캔을 결합하여, 의자 밀집 지역 통과 시 부드러운 S자 자동 우회 기동 구현.

---

## 7. [Phase 3 - v1.4.0~1.4.1 구현 완료] 현장 무인 원클릭 SLAM ↔ Nav2 모드 전환 및 WebUI 제어 체계

* **추진 배경**:
  * 현장(새로운 방, 구조 변경)에서 엔지니어가 SSH 터미널 접속 없이 스마트폰/태블릿 Web UI(`:9999`) 원클릭만으로 "새 지도 작성(SLAM) ➔ 맵 저장 ➔ 자율주행(Nav2)"을 원스톱 수행하기 위한 운영체계.
* **핵심 아키텍처 및 통신 최적화**:
  1. **CycloneDDS Unicast Peer (`rmw_cyclonedds_cpp`, `config/cyclonedds.xml`)**:
     * 기존 FastDDS Discovery Server(SPOF) 및 APIPA 격리 문제를 제거하고 `<LAN_SUBNET>` 대역 고정 + 3-Tier(`127.0.0.1`, `<LAPTOP_IP>`, `<JETSON_IP>`, `<PI5_IP>`) 1:1 유니캐스트 피어링으로 안정화 완료.
  2. **AMCL ↔ SLAM 상호 배타적 프로세스 제어 (`:9999` WebUI)**:
     * `map ➔ odom` TF 좌표계 충돌(Jittering) 방지를 위해 PIN 인증 기반 Nav2/SLAM 상호 인터락 제어 및 무비밀번호 즉시 정지(`nav2_stop`, `slam_stop`), `relocalize`(🎯 위치 찾기), `full_reset`(🔄 주행 전체 초기화) 탑재 완료.
  3. **매핑 조작 인터페이스 (하이브리드 조종 체계)**:
     * **1순위 (실물 OpenCR 조종기)**: 터틀봇 전용 OpenCR 직결 조종기(RC-100 등)를 사용하여 지연 0ms의 정밀한 손맛 주행으로 2~3분 내 왜곡 없는 고품질 맵 스캔. OpenCR의 오도메트리(`/odom`)가 Pi 5 `slam_toolbox`로 실시간 전달되어 맵 자동 완성.
     * **2순위 (Web UI 가상 조이스틱)**: 실물 조종기 부재 시 스마트폰 터치 조작 지원 (워치독 타이머 200ms 무응답 시 자동 정지 가드레일 내장).
  4. **원터치 맵 저장 및 포즈그래프 영구 보존**:
     * `save_active_map.sh`를 통해 일반 Nav2용 `.yaml`/`.pgm`뿐만 아니라 향후 SLAM Localization 모드 재활용을 위한 `.posegraph` 데이터 동시 아카이빙.

---

## 8. [v1.4.9 구현 완료] OpenCR 10.5V 저전압 지속 시 지정 장소(Dock) 자동 복귀

* **목적**: OpenCR 배터리(`/battery_state`)가 **`9.0V < V <= 10.5V` 구간으로 `5초` 이상 지속**되면 순찰·추종을 자동 중지하고 지정된 충전/대기 위치(`dock`)로 자율 복귀.
* **구현 및 안전 가드레일 (Pi 5 `patrol_node.py` + `patrol_waypoints.yaml` + WebUI `/map`)**:
  1. **스위치 OFF(`0.05V`) 오동작 차단**: `9.0V < voltage <= 10.5V` 구간만 저전압으로 판정해 전원 스위치 차단 시 오출발 방지.
  2. **순간 전압 강하(Sag) 필터 & 1회 래치**: `5.0초` 이상 연속 조건으로 기동 피크 강하를 거르고, 복귀 출발 시 래치(`low_volt_returning`)를 걸어 중복 전송 방지 (`11.5V` 이상 회복 시 자동 해제).
  3. **복귀 좌표 관리**: `patrol_waypoints.yaml` `patrol.dock: {x, y, yaw}` 저장 및 WebUI `/map` `🔌 현재 위치 충전소 지정 (10.5V 복귀)` 버튼(`action: set_dock`) 연동 완료 (`DOCK_RETURN` / `DOCKED` + TTS).



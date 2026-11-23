# [관제/모니터링 아키텍처] 젯슨 초경량 YOLO 추론 & 노트북 실시간 HUD 뷰어 사양서

> **문서 버전**: v1.4.9  
> **점검일**: 2026-11-23  
> **작성자**: `ROS2_JW`

### 📋 문서 버전 관리 (Document Version Control)
| 문서&nbsp;버전 | 개정&nbsp;일자 | 작성자 | 문서&nbsp;상태 | 변경 및 개정 내역 |
| :---: | :---: | :---: | :---: | :--- |
| `v1.0.0` | 26.11.03 | ROS2_JW | Approved | • `yolo_monitor_viewer.py` 비대칭 경량 HUD 오버레이 및 18650 배터리·라이다 시각화 명세 제정 |
| `v1.4.0` | 26.11.16 | ROS2_JW | Approved | • `jta_camera` (`640x480`, 15 FPS) 및 `yolo11n` (`detector_node.py`) 최신 패키지 기준 현행화 |
| **`v1.4.9`** | 26.11.23 | **ROS2_JW** | **Approved** | • `v1.4.9` 기준선 정합성 확인 (WebUI `<canvas>` 오버레이와 동일한 비대칭 JSON 분업 구조 유지) |

## 1. 개요 및 설계 배경
기존 구조에서는 젯슨 Orin Nano가 YOLO 추론 결과를 화면에 보여주기 위해 매 프레임 `results[0].plot()` CPU 렌더링 및 `cv2.imencode()` JPEG 재인코딩(프레임당 15~25ms 소모)을 수행하고, 대용량 영상 스트림을 Wi-Fi로 재발행하여 통신 병목 및 젯슨 발열을 유발했습니다.

이를 해결하기 위해 **엣지 추론(젯슨)과 시각화 렌더링(관제 노트북)을 분리하는 비대칭 분업 아키텍처**를 구축했습니다.

```
+-----------------------------------------------------------------------------------+
| [Pi 5 (Tier 1)]                                                                   |
|  - jta_camera 드라이버 (640x480, 15 FPS)                                          |
|    └─► /camera/image_raw/compressed (JPEG Q90 압축 스트림, ~3-5 Mbps)             |
+--------------------------+--------------------------------------------------------+
                           |
            +--------------+--------------+
            |                             |
            ▼                             ▼
+-----------------------+     +-----------------------------------------------------+
| [Jetson Orin Nano]    |     | [노트북 / Host 관제 PC (Tier 3)]                     |
|  - YOLO11n GPU 추론   |     |  - yolo_monitor_viewer.py                           |
|  - 영상 재인코딩 SKIP |     |    ├─ 영상 수신: /camera/image_raw/compressed       |
|    └─► /yolo/detections|     |    ├─ 좌표 수신: /yolo/detections (JSON)            |
|       (초경량 JSON,   |────►|    ├─ 로컬 OpenCV 실시간 바운딩 박스 합성           |
|        0.1ms / <1KB)  |     |    └─ HUD(FPS, 동기화 지연시간, 타겟팅 십자선) 표출 |
+-----------------------+     +-----------------------------------------------------+
```

---

## 2. 노드별 세부 구현 사양

### 2.1 젯슨 추론 노드 (`src/jetson/yolo11n/yolo11n/detector_node.py` / `jetson_yolo_node.py`)
- **주요 역할**: Pi 5의 압축 영상을 받아 YOLO11n GPU 추론 후, 시각화 인코딩을 건너뛰고 **초경량 JSON 토픽만 발행**.
- **토픽 및 파라미터**:
  - 발행 토픽: `/yolo/detections` (`std_msgs/String`)
  - 신규 파라미터: `publish_annotated` (기본값: `False`)
    - `False`: CPU 오버레이 및 JPEG 재인코딩 생략 (지연 20ms 단축, 대역폭 세이브)
    - `True`: 디버깅용 `/yolo/image_annotated/compressed` 동시 발행
- **발행 JSON 데이터 스키마 (`/yolo/detections`)**:
```json
{
  "timestamp": 1730623635.1234,
  "frame_id": "camera_frame",
  "image_size": [640, 480],
  "count": 1,
  "objects": [
    {
      "class_id": 0,
      "class_name": "person",
      "conf": 0.885,
      "box": [120.5, 45.0, 310.2, 420.0],
      "center": [215.3, 232.5],
      "size": [189.7, 375.0],
      "rel_box": [0.1883, 0.0938, 0.4847, 0.875]
    }
  ]
}
```

### 2.2 노트북 실시간 관제 뷰어 (`src/laptop/yolo_monitor_viewer.py`)
- **주요 역할**: 무거운 `rqt`나 복잡한 RViz2 설정 없이, 독립형 초경량 OpenCV GUI로 프레임과 JSON 좌표를 실시간 합성.
- **핵심 특징**:
  1. **초저지연 QoS (Real-time Best-Effort / Depth 1)**: 무선 환경 패킷 지연 및 큐 밀림 차단.
  2. **독립 30 FPS 렌더링 루프**: 통신 콜백과 GUI 렌더링 스레드를 분리하여 화면 끊김 없는 매끄러운 디스플레이.
  3. **엔지니어링 HUD 대시보드**:
     - 실시간 수신 FPS 및 해상도
     - 젯슨 타임스탬프와 카메라 프레임 간의 동기화 지연시간 (`Sync Latency ms`, 100ms 초과 시 경고 색상 전환)
     - 검출된 객체 수, 타겟 중심점 십자선 표출 (추종 제어 정렬 확인용)
  4. **단축키 지원**: `Q` (종료), `S` (현재 화면 스크린샷 캡처 저장)

---

## 3. 정량적 엔지니어링 개선 효과

| 항목 | 기존 (젯슨 재인코딩 방식) | 개선 (노트북 오버레이 분업 방식) | 개선 효과 |
| :--- | :--- | :--- | :--- |
| **젯슨 파이프라인 지연** | 35 ~ 50 ms | **15 ~ 25 ms** | **약 20ms 즉각 단축 (추론 속도 2배 체감)** |
| **젯슨 CPU 부하** | 높음 (OpenCV plot + imencode) | **최저 (순수 JSON 직렬화 0.1ms)** | CPU 발열 및 스로틀링 억제 |
| **무선 Wi-Fi 대역폭** | 영상 2개 스트림 (~10-15 Mbps) | **영상 1개 + JSON 1KB (~3-5 Mbps)** | 대역폭 60% 이상 절감, 패킷 드롭 방지 |
| **관제 도구 편의성** | rqt 의존 (무겁고 프리징 잦음) | **독립 OpenCV 뷰어 (0.3초 즉시 기동)** | 지연시간/FPS 실시간 HUD 진단 가능 |

---

## 4. 실행 및 운용 가이드

### 젯슨에서 YOLO 노드 구동
```bash
# 기본 초경량 JSON 모드로 기동 (권장)
ros2 run jta_jetson jetson_yolo_node.py

# 필요 시 영상 재인코딩 모드 활성화 (디버깅용)
ros2 run jta_jetson jetson_yolo_node.py --ros-args -p publish_annotated:=true
```

### 노트북에서 관제 뷰어 기동
```bash
python3 <REPO_ROOT>/src/laptop/yolo_monitor_viewer.py
```
- 화면 좌상단에 FPS와 Latency가 표시되며, `q` 입력 시 안전하게 종료됩니다.

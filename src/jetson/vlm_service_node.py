#!/usr/bin/env python3
"""
[Tier 2 Jetson Orin Nano] Qwen2-VL 상시 가동(Continuous) & YOLO 유기적 연동 브리핑 노드
- 기동 시 Qwen2-VL-2B-Instruct 4-bit NF4 가중치를 GPU VRAM(1.42GB)에 상주
- 상시 연속 추론 루프(Continuous Loop): 6~7초 주기로 최신 카메라 + YOLO 팩트 결합 인지
- 1. 상태 변화 감지 자동 발화 (State-Change Voice): 사물/사람 수량 및 종류 변경 시 선희 성우 자동 발화
- 2. 무지연 0초 즉답 캐싱 (Zero-Latency Answer): 수동 질의 수신 시 최신 분석 결과 0.01초 즉답
- 3. 정기 인터벌 브리핑 (Periodic Interval): 120초(2분) 주기 무변화 시 정기 현장 음성 보고
- 2-Tier 바운딩박스 좌표 괄호 정규식 및 따옴표 제거 방탄 처리
"""

import time
import re
import json
import threading
from collections import Counter
import cv2
import numpy as np
from PIL import Image

import torch
from transformers import Qwen2VLForConditionalGeneration, AutoProcessor, BitsAndBytesConfig
from qwen_vl_utils import process_vision_info

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import CompressedImage, LaserScan
from std_msgs.msg import String


# COCO 주요 클래스 한국어 매핑 딕셔너리
CLASS_KR_MAP = {
    'person': '사람',
    'chair': '의자',
    'desk': '책상',
    'dining table': '테이블',
    'laptop': '노트북',
    'cell phone': '휴대폰',
    'bottle': '물병',
    'cup': '컵',
    'backpack': '가방',
    'handbag': '핸드백',
    'suitcase': '가방/캐리어',
    'book': '책',
    'keyboard': '키보드',
    'mouse': '마우스',
    'tv': '모니터/TV',
    'bed': '침대',
    'couch': '소파',
    'potted plant': '화분',
    'motorcycle': '오토바이',
    'bicycle': '자전거',
    'car': '자동차',
    'cat': '고양이',
    'dog': '강아지',
    'clock': '시계',
    'remote': '리모컨',
    'refrigerator': '냉장고',
    'sports ball': '공',
    'umbrella': '우산',
    'scissors': '가위',
    'teddy bear': '인형',
    'sink': '세면대',
    'oven': '오븐',
    'microwave': '전자레인지',
}


def clean_speech_text(text: str) -> str:
    """VLM 및 TTS 음성 발화 전용 방탄 텍스트 정제기
    1. 중국식 전각 문장부호 및 특수문자 제거
    2. 불필요한 인트로(영상에서:, 화면에서: 등) 제거
    3. 바운딩 박스/그라운딩 좌표(완성형/미완성형 잘림 일체) 완전 박멸
    4. 영단어 -> 표준 한국어 명칭 치환
    5. Qwen2-VL 특수 오역(커플 -> 컵) 보정
    6. 연속 쉼표 및 공백 정리
    7. 자연스러운 구어체 종결형(~가 보입니다, ~있습니다 등) 보정
    """
    if not text:
        return ""

    # 1. 중국식 전각 문장부호 정규화
    text = text.replace('，', ', ').replace('。', '. ').replace('、', ', ').replace('：', ': ')

    # 2. 인트로 사족 제거
    text = re.sub(r'^(에디터에서|화면에서|사진에서|영상에서|센서에서|카메라에서)[^:]*:\s*', '', text)

    # 3. 바운딩 박스 / 좌표 괄호 완전 제거
    # 3-1. 닫힌 괄호 좌표: (123, 456), [123, 456], {123, 456} 등
    text = re.sub(r'[\(\[\{][0-9\s,.]+[\)\]\}]', '', text)
    # 3-2. 토큰 제한으로 잘린 미완성 끝 괄호: (929., (995, 72. 등
    text = re.sub(r'[\(\[\{][0-9\s,.]*$', '', text)
    # 3-3. 괄호 없는 잔류 좌표 쌍 (예: 995, 720)
    text = re.sub(r'\b[0-9]{3,4}\s*,\s*[0-9]{3,4}\b', '', text)

    # 4. 영단어 -> 자연스러운 한국어 표준 명칭 치환 (CLASS_KR_MAP + 추가 별칭 통합)
    extra_en_kr = {
        r'\bpeople\b': '사람들',
        r'\bcell\s*phone\b': '휴대폰',
        r'\bphone\b': '휴대폰',
        r'\btable\b': '테이블',
        r'\bbag\b': '가방',
        r'\bmonitor\b': '모니터',
        r'\bdoor\b': '문',
    }
    for eng, kr in CLASS_KR_MAP.items():
        text = re.sub(rf'\b{re.escape(eng)}\b', kr, text, flags=re.IGNORECASE)
    for pat, kr in extra_en_kr.items():
        text = re.sub(pat, kr, text, flags=re.IGNORECASE)

    # 5. 특수 오역 단어 보정 (Qwen2-VL이 cup을 커플로 오인 발화하는 현상 방지)
    text = re.sub(r'\b커플\s*([0-9]+개)\b', r'컵 \1', text)

    # 6. 연속 쉼표 및 공백 정리
    text = re.sub(r'[,،]+', ',', text)
    text = re.sub(r'\s*,\s*', ', ', text)
    text = re.sub(r'[\r\n\t]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    # 7. 앞뒤 잔여 부호 정리
    text = text.strip(' ,;:-.')

    # 8. 어색한 문장 종결 보정 ('~개' 또는 '~명'으로 끝나면 자연스러운 종결형 부착)
    if text:
        if re.search(r'(개|명|대|권|병|장)$', text):
            text += '가 보입니다.'
        elif not text.endswith(('.', '!', '?')):
            text += '.'

    return text


class JetsonVLMResidentNode(Node):
    def __init__(self):
        super().__init__('jetson_vlm_service')
        self.get_logger().info(">>> [VLM Service] Initializing Qwen2-VL Continuous Engine...")

        # 상태 및 동기화 락
        self.latest_jpeg_bytes = None
        self.frame_lock = threading.Lock()
        
        self.latest_yolo_data = None
        self.latest_yolo_counts = {}
        self.yolo_lock = threading.Lock()

        self.inference_lock = threading.Lock()
        self.is_inferencing = False
        self.is_running = True
        self.declare_parameter('background_loop_enabled', False)
        self.enable_background_loop = bool(self.get_parameter('background_loop_enabled').value)
        self.background_cooldown_sec = 25.0  # 백그라운드 루프 활성화 시 쿨다운 (기본 OFF: 순수 On-Demand)

        # 캐시 및 발화 상태 관리
        self.latest_summary = "로봇 주변 시야를 확인 중입니다."
        self.last_spoken_counts = None
        self.last_spoken_proximity = False
        self.last_spoken_time = time.time()
        self.periodic_interval_sec = 120.0  # 2분 정기 순찰 인터벌 브리핑
        self.min_cooldown_sec = 20.0        # 옵션 B: 채터링 방지 최소 20초 쿨다운

        # 라이다 전방 거리 감시 (옵션 1: 1.5m 이내 사람 접근 감지)
        self.latest_front_dist = 999.0
        self.scan_lock = threading.Lock()

        # 라이다 (/scan) 구독: 반드시 BEST_EFFORT QoS 적용 (AGENTS.md 헌법 준수)
        scan_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )
        self.scan_sub = self.create_subscription(
            LaserScan,
            '/scan',
            self.scan_callback,
            scan_qos
        )

        # 1. 최신 카메라 프레임 구독 (depth=1, 최신 프레임 유지)
        self.cam_sub = self.create_subscription(
            CompressedImage,
            '/camera/image_raw/compressed',
            self.camera_callback,
            1
        )

        # 2. YOLO 실시간 감지 데이터 구독 (/yolo/detections)
        self.yolo_sub = self.create_subscription(
            String,
            '/yolo/detections',
            self.yolo_callback,
            10
        )

        # 3. VLM 질의 트리거 구독 (/vlm/trigger)
        self.trigger_sub = self.create_subscription(
            String,
            '/vlm/trigger',
            self.trigger_callback,
            10
        )

        # 4. VLM 최종 응답 발화 발행 (/vlm/response) -> Pi 5 스피커 수신
        self.response_pub = self.create_publisher(
            String,
            '/vlm/response',
            10
        )

        # 4-1. CLI 전용 조용한 채팅 질의/응답 (/vlm/chat_query -> /vlm/chat_response, 스피커 미발화)
        self.chat_sub = self.create_subscription(
            String,
            '/vlm/chat_query',
            self.chat_callback,
            10
        )
        self.chat_pub = self.create_publisher(
            String,
            '/vlm/chat_response',
            10
        )

        # 5. 최신 실시간 요약 캐시 토픽 발행 (/vlm/latest_summary) -> WebUI 등 참조
        self.summary_pub = self.create_publisher(
            String,
            '/vlm/latest_summary',
            10
        )

        # 6. 효과음/음악 재생 요청 발행 (/play/sound) -> Pi 5 스피커 수신
        self.sound_pub = self.create_publisher(
            String,
            '/play/sound',
            10
        )
        self.last_sound_time = 0.0
        self.sound_cooldown_sec = 50.0
        self.sound_confirm_sec = 3.0
        self.sound_delay_sec = 0.0
        self.close_person_since = None
        self.close_person_last_seen = 0.0
        self.pending_sound_at = None
        self.sound_timer = self.create_timer(0.1, self._sound_timer_cb)

        # GPU VRAM 모델 1회 상주 로드
        self.load_model()
        self.get_logger().info(">>> [VLM Service] Model Resident in GPU VRAM! Ready for queries.")

        # 7. 백그라운드 연속 추론 루프 (기본 OFF: GPU 발열·YOLO 간섭·락 대기 원천 차단, 순수 On-Demand 운영)
        if self.enable_background_loop:
            self.worker_thread = threading.Thread(target=self._continuous_loop, daemon=True)
            self.worker_thread.start()
            self.get_logger().info(f">>> [VLM Service] Continuous Loop Worker Started (Cooldown: {self.background_cooldown_sec}s)")
        else:
            self.worker_thread = None
            self.get_logger().info(">>> [VLM Service] Pure On-Demand Mode (4-bit VRAM resident, background loop OFF)")

    def load_model(self):
        self.get_logger().info(">>> [VLM Service] Loading Qwen2-VL-2B-Instruct into GPU VRAM (4-bit NF4)...")
        model_id = "Qwen/Qwen2-VL-2B-Instruct"
        cache_dir = "/root/ros2_ws/models"

        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True
        )

        t0 = time.time()
        self.model = Qwen2VLForConditionalGeneration.from_pretrained(
            model_id,
            quantization_config=bnb_config,
            device_map="cuda:0",
            cache_dir=cache_dir
        )
        self.processor = AutoProcessor.from_pretrained(model_id, cache_dir=cache_dir)
        load_sec = time.time() - t0
        allocated = torch.cuda.memory_allocated() / (1024 ** 3)
        self.get_logger().info(f">>> [VLM Service] 4-bit Model loaded successfully in {load_sec:.2f}s! (VRAM: {allocated:.2f}GB)")

    def camera_callback(self, msg: CompressedImage):
        # 매초 15회 불필요한 cv2.imdecode 제거: 원본 JPEG 바이트만 O(1) 보관 후 추론 직전 1회 디코딩
        if msg.data:
            raw = bytes(msg.data)
            with self.frame_lock:
                self.latest_jpeg_bytes = raw

    def scan_callback(self, msg: LaserScan):
        """LDS-01 라이다 전방 60도 부채꼴 콘(±30도) 최소 실측 거리 산출"""
        try:
            ranges = msg.ranges
            n = len(ranges)
            if n < 360:
                return
            # 전방 60도 부채꼴 콘: 정면 0도 기준 좌우 30도 (330~359도 및 0~30도)
            front_slices = list(ranges[0:30]) + list(ranges[330:360])
            valid = [
                r for r in front_slices
                if msg.range_min < r < msg.range_max and not (np.isnan(r) or np.isinf(r))
            ]
            min_dist = min(valid) if valid else 999.0
            with self.scan_lock:
                self.latest_front_dist = float(min_dist)
        except Exception:
            pass

    def yolo_callback(self, msg: String):
        try:
            data = json.loads(msg.data)
            objects = data.get("objects", [])
            counts = Counter([obj.get("class_name", "") for obj in objects if obj.get("class_name")])
            with self.yolo_lock:
                self.latest_yolo_data = data
                self.latest_yolo_counts = dict(counts)

            # 사람 바운딩 박스 세로 높이(h >= 250px, conf >= 0.50) 기반 1.2m 근접 3초 확정 판정 (patrol_node와 기준 일치)
            person_objs = [
                obj for obj in objects
                if obj.get("class_name") == "person" and float(obj.get("conf", 1.0)) >= 0.50
            ]

            is_person_close = False
            max_person_h = 0.0
            for p in person_objs:
                box = p.get("box", p.get("bbox", []))
                if len(box) == 4:
                    h = float(box[3]) - float(box[1])
                    if h > max_person_h:
                        max_person_h = h
                    if h >= 250.0:  # 640x480 해상도 기준 약 1.2m 이내 근접 판정 (높이 250px 이상)
                        is_person_close = True
                        break

            now = time.monotonic()
            if (
                is_person_close
                and self.pending_sound_at is None
                and (self.last_sound_time == 0.0 or (now - self.last_sound_time >= self.sound_cooldown_sec))
            ):
                if self.close_person_since is None or (now - self.close_person_last_seen) > 0.8:
                    self.close_person_since = now
                self.close_person_last_seen = now
                if (now - self.close_person_since) >= self.sound_confirm_sec:
                    self.close_person_since = None
                    self.last_sound_time = now
                    self.pending_sound_at = now + self.sound_delay_sec
                    self.get_logger().info(
                        f"⏳ [Vision 1.2m Proximity] Confirmed >= {self.sound_confirm_sec:.0f}s (Height: {max_person_h:.1f}px) -> Playing dobingda.mp3 (cooldown {self.sound_cooldown_sec:.0f}s)"
                    )
            elif not is_person_close and self.close_person_since is not None and (now - self.close_person_last_seen) > 0.8:
                self.close_person_since = None
        except Exception:
            pass

    def _sound_timer_cb(self):
        if self.pending_sound_at is None:
            return
        now = time.monotonic()
        if now >= self.pending_sound_at:
            self.pending_sound_at = None
            sound_msg = String()
            sound_msg.data = os.environ.get("PI5_SOUND_DOBINGDA", "dobingda")
            self.sound_pub.publish(sound_msg)
            self.get_logger().info(
                "⚡ [Vision 1.2m Proximity] Playing dobingda.mp3!"
            )

    def _build_situation_prompt(self, allow_person_alert: bool = True) -> tuple[str, bool]:
        """상황 브리핑 프롬프트 생성 (수동 요청 시 YOLO 개입 없이 순수 VLM, 백그라운드만 1.5m 사람 경보)"""
        if allow_person_alert:
            with self.yolo_lock:
                yolo_counts = dict(self.latest_yolo_counts)
            with self.scan_lock:
                front_dist = self.latest_front_dist

            cur_person = yolo_counts.get('person', 0)
            if (front_dist <= 1.5) and (cur_person > 0):
                prompt = (
                    f"라이다 실측 팩트: 전방 {front_dist:.1f}m 거리에 사람 {cur_person}명 접근 중. "
                    "위 실측 데이터를 바탕으로, 부연설명, 괄호, 좌표 없이 "
                    f"'전방 {front_dist:.1f}미터 거리에 사람이 감지되었습니다.'처럼 간결하고 자연스러운 한국어 한 문장으로만 말하세요."
                )
                return prompt, True

        prompt = (
            "Look closely at this image and describe only what is actually visible "
            "(people, furniture, or objects) in one short, natural Korean sentence without guessing."
        )
        return prompt, False

    def trigger_callback(self, msg: String):
        """수동 질의 및 이벤트 트리거 핸들러 (호출 순간의 최신 프레임 즉시 추론)"""
        prompt_text = msg.data.strip()

        is_briefing = (not prompt_text or prompt_text == "즉시")
        if is_briefing:
            prompt_text, _ = self._build_situation_prompt(allow_person_alert=False)
            self.get_logger().info(">>> [On-Demand Trigger] Running fresh inference on current frame...")
        else:
            prompt_text = (
                f"{prompt_text} "
                "(Answer directly based only on what you see in the image, in one short natural Korean sentence.)"
            )

        threading.Thread(
            target=self._run_on_demand_query,
            args=(prompt_text, is_briefing),
            daemon=True,
        ).start()

    def chat_callback(self, msg: String):
        """CLI 채팅 전용 질의 핸들러 (스피커 미발화, 이미지 기반 사실 질의 앵커링)"""
        raw = msg.data.strip()
        if not raw or raw == "즉시":
            prompt_text = "첨부된 사진을 보고 화면에 보이는 사물과 상황을 사실 그대로 한국어로 설명하세요."
        else:
            prompt_text = (
                "첨부된 사진을 직접 보고 관찰된 시각적 사실만으로 다음 질문에 한국어로 답하세요. "
                f"질문: {raw}"
            )
        threading.Thread(
            target=self._run_chat_query,
            args=(prompt_text,),
            daemon=True,
        ).start()

    def _run_chat_query(self, custom_prompt: str):
        # 추론 중 연타로 스레드 무한 누적 방지 (non-blocking 락 체크)
        if not self.inference_lock.acquire(blocking=False):
            self.get_logger().warn(">>> [CLI Chat Query] VLM busy -> dropping duplicate request.")
            out_msg = String()
            out_msg.data = "(이전 VLM 분석을 처리 중입니다. 잠시 후 다시 질문해 주세요.)"
            self.chat_pub.publish(out_msg)
            return
        try:
            self.get_logger().info(f">>> [CLI Chat Query] Prompt: '{custom_prompt}'")
            resp = self._execute_vlm_inference(custom_prompt, max_new_tokens=96)
            out_msg = String()
            out_msg.data = resp if resp else "(현재 카메라 프레임이 없거나 추론에 실패했습니다.)"
            self.chat_pub.publish(out_msg)
        finally:
            self.inference_lock.release()

    def _run_on_demand_query(self, custom_prompt: str, is_briefing: bool = False):
        if not self.inference_lock.acquire(blocking=False):
            self.get_logger().warn(">>> [On-Demand Query] VLM busy -> dropping duplicate request.")
            return
        try:
            self.get_logger().info(f">>> [On-Demand Query] Prompt: '{custom_prompt}'")
            resp = self._execute_vlm_inference(custom_prompt)
            if resp:
                if is_briefing and "사람" not in resp:
                    with self.yolo_lock:
                        has_person = self.latest_yolo_counts.get('person', 0) > 0
                    if has_person:
                        resp = f"사람이 보이고, {resp}"
                self.latest_summary = resp
                summary_msg = String()
                summary_msg.data = resp
                self.summary_pub.publish(summary_msg)
                resp_msg = String()
                resp_msg.data = resp
                self.response_pub.publish(resp_msg)
        finally:
            self.inference_lock.release()

    def _continuous_loop(self):
        """[옵션 백그라운드 추론 엔진] background_loop_enabled=True 시에만 동작"""
        self.get_logger().info(">>> [Continuous Engine] Continuous perception loop running...")
        time.sleep(3.0)  # 초기 센서 버퍼 안정화 대기

        while self.is_running and rclpy.ok():
            with self.frame_lock:
                frame_available = self.latest_jpeg_bytes is not None

            if not frame_available:
                time.sleep(1.0)
                continue

            # 1. 최신 YOLO 감지 및 라이다 전방 거리 결합 (공통 프롬프트 생성기)
            prompt, person_within_1_5m = self._build_situation_prompt()

            # 2. VLM 상시 추론 수행 (Inference Lock 보호)
            with self.inference_lock:
                clean_resp = self._execute_vlm_inference(prompt)

            if not clean_resp:
                time.sleep(2.0)
                continue

            # 3. 최신 결과 캐시 갱신 (WebUI 수동 브리핑 0초 즉답용)
            self.latest_summary = clean_resp
            summary_msg = String()
            summary_msg.data = clean_resp
            self.summary_pub.publish(summary_msg)

            # 4. 자동 음성 발화 차단 (형님 오더: 2분 주기 및 변동 자동 발화 OFF, WebUI 버튼 클릭 시에만 발화)
            self.last_spoken_proximity = person_within_1_5m

            # 추론 후 발열/GPU 점유 절감용 여유 쿨다운 (온디맨드·이벤트 우선, 기본 25초)
            time.sleep(self.background_cooldown_sec)

    def _execute_vlm_inference(self, prompt: str, max_new_tokens: int = 40) -> str:
        """Qwen2-VL 실제 추론 및 좌표/노이즈 방탄 필터링 공통 함수"""
        try:
            with self.frame_lock:
                raw_jpeg = self.latest_jpeg_bytes
            if not raw_jpeg:
                return ""

            # 락 밖에서 1회만 JPEG 디코딩·리사이즈·RGB 변환 (camera_callback 블로킹 0%)
            frame = cv2.imdecode(np.frombuffer(raw_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                return ""
            if frame.shape[1] != 640 or frame.shape[0] != 480:
                frame = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_AREA)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb_frame)

            t_start = time.time()
            messages = [
                {
                    "role": "system",
                    "content": (
                        "당신은 자율주행 순찰 로봇의 인공지능 안내자입니다. "
                        "항상 정중하고 명확한 한국어 표준어 구어체 한 문장으로만 답변하십시오. "
                        "좌표, 바운딩 박스, 괄호 숫자, 영단어, 특수문자는 절대 출력하지 마십시오."
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": img},
                        {"type": "text", "text": prompt}
                    ]
                }
            ]

            text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            image_inputs, video_inputs = process_vision_info(messages)
            inputs = self.processor(
                text=[text],
                images=image_inputs,
                videos=video_inputs,
                padding=True,
                return_tensors="pt"
            ).to("cuda")

            with torch.no_grad():
                generated_ids = self.model.generate(**inputs, max_new_tokens=max_new_tokens, repetition_penalty=1.2)

            generated_ids_trimmed = [
                out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
            ]
            response = self.processor.batch_decode(
                generated_ids_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )[0].strip()

            # 2-Tier 방탄 텍스트 정제기 적용
            clean_resp = clean_speech_text(response)

            latency = time.time() - t_start
            self.get_logger().info(f">>> [VLM Inferred in {latency:.2f}s] Result: {clean_resp}")
            return clean_resp

        except Exception as e:
            self.get_logger().error(f"VLM Inference error: {e}")
            return ""


def main(args=None):
    rclpy.init(args=args)
    node = JetsonVLMResidentNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.is_running = False
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()


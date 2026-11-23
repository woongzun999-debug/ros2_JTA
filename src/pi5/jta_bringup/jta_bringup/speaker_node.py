#!/usr/bin/env python3
"""
[Tier 1 Raspberry Pi 5] 현장 USB 스피커 고품질 한국어 음성 출력(Edge-TTS) ROS 2 노드
- 대상 기기: Raspberry Pi 5 (<PI5_IP>) Distrobox humble
- 하드웨어: USB 오디오 스피커 (UACDemoV1.0, plughw:0,0)
- 구독 토픽:
  1) /vlm/response (std_msgs/String): Jetson 4-bit VLM 현장 시각 브리핑 텍스트
  2) /tts/say (std_msgs/String): 관제 웹UI 또는 시스템 경보 음성 텍스트
- 엔진: Microsoft Edge-TTS (성우급 여성 보이스: ko-KR-SunHiNeural, 속도 +30% 고속)
- 재생: mpg123
"""

import os
import re
import time
import random
import asyncio
import subprocess
import threading
import edge_tts
import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Int32


def clean_speech_text(text: str) -> str:
    """VLM 및 TTS 음성 발화 전용 방탄 텍스트 정제기 (2-Tier 방탄 필터)
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

    # 4. 영단어 -> 자연스러운 한국어 표준 명칭 치환
    en_kr = {
        r'\bcup\b': '컵',
        r'\bchair\b': '의자',
        r'\bperson\b': '사람',
        r'\bpeople\b': '사람들',
        r'\bbottle\b': '물병',
        r'\blaptop\b': '노트북',
        r'\bcell\s*phone\b': '휴대폰',
        r'\bphone\b': '휴대폰',
        r'\btable\b': '테이블',
        r'\bdesk\b': '책상',
        r'\bbackpack\b': '가방',
        r'\bbag\b': '가방',
        r'\bmonitor\b': '모니터',
        r'\bmouse\b': '마우스',
        r'\bkeyboard\b': '키보드',
        r'\bbook\b': '책',
        r'\bdoor\b': '문',
    }
    for pat, kr in en_kr.items():
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


class Pi5SpeakerNode(Node):
    SUPPORTED_BOOT_EXTS = (".mp3", ".wav")

    def __init__(self):
        super().__init__('pi5_speaker_node')
        self.get_logger().info("🔊 [Pi5 Speaker Node] Initializing Edge-TTS Speaker Node...")

        self.speech_lock = threading.Lock()
        self.proc_lock = threading.Lock()
        self.current_audio_proc = None
        self.audio_device = "plug:dmix:CARD=UACDemoV10,DEV=0"
        self.temp_mp3 = "/tmp/pi5_edge_tts.mp3"
        self.voice = "ko-KR-SunHiNeural"  # Microsoft 선희 (전문 성우급 또렷한 여성 음성)
        self.rate = "+0%"                  # 성우 본연의 자연스럽고 편안한 1.0배속 표준 발화

        # 1. 젯슨 VLM 시각 브리핑 결과 구독
        self.vlm_sub = self.create_subscription(
            String,
            '/vlm/response',
            self.speech_callback,
            10
        )

        # 2. 범용 시스템/경보 음성 발화 구독
        self.tts_sub = self.create_subscription(
            String,
            '/tts/say',
            self.speech_callback,
            10
        )

        # 3. 효과음/음악 파일 재생 구독 (/play/sound)
        self.sound_sub = self.create_subscription(
            String,
            '/play/sound',
            self.sound_callback,
            10
        )

        # 4. 🎚️ [현장 USB 스피커 볼륨 제어 토픽 연동] (/speaker/volume_set, /speaker/volume_state)
        self.boot_music_volume = 80
        self.ready_volume = 95
        self.current_volume = self._get_hardware_volume()
        self.vol_state_pub = self.create_publisher(
            Int32,
            '/speaker/volume_state',
            10
        )
        self.vol_sub = self.create_subscription(
            Int32,
            '/speaker/volume_set',
            self.volume_callback,
            10
        )
        self.vol_timer = self.create_timer(3.0, self._broadcast_volume_state)

        # 5. 🎶 [부팅 세레머니 가드레일] (A안: 완곡 끝날 때까지 완전 대기 & 외부 입력 차단)
        # /dev/shm은 100% RAM tmpfs이므로 Pi 5 재부팅 시 확실히 비워져 최초 부팅 여부 정확히 감지
        self.boot_flag_file = "/dev/shm/pi5_boot_ceremony_done"
        self.boot_song_dir = os.path.expanduser("~/music/song")
        self.boot_song_path = None
        self.is_booting = False

        if not os.path.exists(self.boot_flag_file):
            self.boot_song_path = self._select_boot_song()

        if self.boot_song_path:
            self.is_booting = True
            self.get_logger().info(
                f"🎵 [Boot Ceremony] Fresh boot detected! Starting startup ceremony ({self.boot_song_path})..."
            )
            threading.Thread(target=self._boot_ceremony_worker, daemon=True).start()
        else:
            if not os.path.exists(self.boot_flag_file):
                self.get_logger().warn(
                    f"⚠️ [Boot Ceremony] No playable audio (.mp3/.wav) in '{self.boot_song_dir}'. Skipping boot song."
                )
            self.get_logger().info("🔊 [Pi5 Speaker Node] System READY. Normal operations enabled.")

        self.get_logger().info(f"🔊 [Pi5 Speaker Node] Edge-TTS Ready (Voice: {self.voice}, Rate: {self.rate}, Vol: {self.current_volume}%)")

    def _get_hardware_volume(self) -> int:
        try:
            res = subprocess.run(["amixer", "-c", "0", "sget", "PCM"], capture_output=True, text=True, check=False)
            m = re.search(r'\[([0-9]+)%\]', res.stdout)
            if m:
                return int(m.group(1))
        except Exception:
            pass
        return 85

    def _broadcast_volume_state(self):
        try:
            msg = Int32()
            msg.data = self.current_volume
            self.vol_state_pub.publish(msg)
        except Exception:
            pass

    def _set_hardware_volume(self, vol: int):
        vol = max(0, min(100, int(vol)))
        self.get_logger().info(f"🔊 [Pi5 Speaker] Setting hardware volume to {vol}%...")
        try:
            cmd = ["amixer", "-c", "0", "sset", "PCM", f"{vol}%"]
            subprocess.run(cmd, check=False)
            self.current_volume = vol
            self._broadcast_volume_state()
        except Exception as e:
            self.get_logger().error(f"❌ [Pi5 Speaker] Volume set error: {e}")

    def volume_callback(self, msg: Int32):
        self._set_hardware_volume(msg.data)

    def _select_boot_song(self) -> str | None:
        """부팅 음악 디렉터리에서 재생 가능한 오디오 파일을 동적으로 탐색해 무작위 선택"""
        if not os.path.isdir(self.boot_song_dir):
            return None

        candidates = []
        try:
            for name in os.listdir(self.boot_song_dir):
                if name.startswith("."):
                    continue
                full_path = os.path.join(self.boot_song_dir, name)
                if os.path.isfile(full_path) and name.lower().endswith(self.SUPPORTED_BOOT_EXTS):
                    candidates.append(full_path)
        except OSError as e:
            self.get_logger().warn(f"⚠️ [Boot Ceremony] Failed to scan '{self.boot_song_dir}': {e}")
            return None

        if not candidates:
            return None
        return random.choice(candidates)

    def _build_audio_cmd(self, file_path: str) -> list[str]:
        """파일 확장자에 맞는 오디오 재생 명령어 생성 (.wav -> aplay, 그 외 -> mpg123)"""
        if file_path.lower().endswith('.wav'):
            return ["aplay", "-q", "-D", self.audio_device, file_path]
        return ["mpg123", "-q", "-a", self.audio_device, file_path]

    def _speak_tts(self, text: str, context: str = "Pi5 Speaker"):
        """Edge-TTS로 음성을 생성해 USB 스피커로 동기 재생"""
        try:
            asyncio.run(self._generate_audio(text))
            tts_cmd = ["mpg123", "-q", "-a", self.audio_device, self.temp_mp3]
            subprocess.run(tts_cmd, check=False)
        except Exception as e:
            self.get_logger().error(f"❌ [{context}] Edge-TTS error: {e}")

    def _boot_ceremony_worker(self):
        """Pi 5 부팅 세레머니 워커 (A안)
        1. 하드웨어 및 ROS 2 통신 안정화 대기 (3초)
        2. 부팅 음악 볼륨(80%) 설정 후 인트로 TTS('노래 한 곡 듣고 가시죠.') 발화 및 1초 대기
        3. 선택된 부팅 음악 완곡 재생 (끝날 때까지 블로킹, 다른 모든 음성/감지 차단)
        4. 완곡 종료 직후 운용 볼륨(95%) 상향 후 Edge-TTS로 '시스템을 동작합니다.' 공식 발화
        5. 시스템 READY 상태 전환 (is_booting = False) 및 세레머니 완료 플래그 기록
        """
        ready_volume_applied = False
        try:
            time.sleep(3.0)  # 하드웨어 및 ROS 2 그래프 안정화 대기

            self.get_logger().info(
                f"🎵 [Boot Ceremony] 🎶 Playing boot song: '{self.boot_song_path}' "
                f"(Full Track, Vol: {self.boot_music_volume}%)"
            )
            with self.speech_lock:
                # 1. 부팅 음악 볼륨 80% 적용 후 인트로 TTS 발화 및 1초 대기
                self._set_hardware_volume(self.boot_music_volume)
                intro_phrase = "노래 한 곡 듣고 가시죠."
                self.get_logger().info(f"🔊 [Boot Ceremony] Speaking: '{intro_phrase}' (Vol: {self.boot_music_volume}%)")
                self._speak_tts(intro_phrase, context="Boot Ceremony")
                time.sleep(1.0)

                # 2. 선택된 부팅 곡 완곡 재생 (외부 stop 요청 시 즉시 중단 후 다음 단계 진행)
                cmd = self._build_audio_cmd(self.boot_song_path)
                self._run_audio_proc(cmd)
                self.get_logger().info("🎵 [Boot Ceremony] Boot song finished (or skipped by stop)!")

                # 3. 안내 멘트 발화 직전 운용 볼륨 95% 상향
                self._set_hardware_volume(self.ready_volume)
                ready_volume_applied = True

                # 4. 안내 멘트 발화: '시스템을 동작합니다.'
                ready_phrase = "시스템을 동작합니다."
                self.get_logger().info(f"🔊 [Boot Ceremony] Speaking: '{ready_phrase}' (Vol: {self.ready_volume}%)")
                self._speak_tts(ready_phrase, context="Boot Ceremony")

            # 4. 부팅 세레머니 완료 플래그 기록 (/dev/shm은 재부팅 시 자동 소거)
            try:
                with open(self.boot_flag_file, 'w') as f:
                    f.write(f"boot_completed_at_{time.time()}\n")
            except Exception as fe:
                self.get_logger().warn(f"⚠️ [Boot Ceremony] Could not write flag file: {fe}")

        except Exception as e:
            self.get_logger().error(f"❌ [Boot Ceremony] Fatal worker error: {e}")
        finally:
            if not ready_volume_applied:
                self._set_hardware_volume(self.ready_volume)
            self.is_booting = False
            self.get_logger().info("🚀 [Boot Ceremony] System is now fully ACTIVE and ARMED! All sound/speech enabled.")

    def _run_audio_proc(self, cmd: list[str]):
        """오디오 프로세스를 실행하고 종료 신호(stop)로 즉시 중단할 수 있게 추적"""
        with self.proc_lock:
            self.current_audio_proc = subprocess.Popen(cmd)
            proc = self.current_audio_proc
        try:
            proc.wait()
        finally:
            with self.proc_lock:
                if self.current_audio_proc is proc:
                    self.current_audio_proc = None

    def stop_current_audio(self):
        """현재 재생 중인 음악/효과음(부팅 세레머니 포함)을 즉시 종료"""
        with self.proc_lock:
            proc = self.current_audio_proc
        if proc is not None and proc.poll() is None:
            self.get_logger().info("⏹️ [Pi5 Speaker] Stopping active audio playback...")
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()

    def sound_callback(self, msg: String):
        sound_req = msg.data.strip()
        if not sound_req:
            return
        if sound_req.lower() == "stop":
            self.stop_current_audio()
            return

        if self.is_booting:
            self.get_logger().info("⏳ [Pi5 Sound] Blocked: Boot ceremony in progress. Sound request dropped.")
            return

        threading.Thread(target=self._play_sound_worker, args=(sound_req,), daemon=True).start()

    def _play_sound_worker(self, sound_req: str):
        # 중복 음악 겹침 방지: 기존 음악만 정리하고 speech_lock 없이 dmix로 스무스하게 재생
        self.stop_current_audio()
        sound_map = {
            "dobingda": os.path.expanduser("~/music/dobingda.mp3"),
            "bingdari": os.path.expanduser("~/music/dobingda.mp3"),
            "bingdari_wav": os.path.expanduser("~/music/bingdari.wav"),
            "neon": os.path.expanduser("~/music/neon.mp3"),
        }
        file_path = os.path.expanduser(sound_map.get(sound_req.lower(), sound_req))

        if not os.path.exists(file_path):
            self.get_logger().error(f"❌ [Pi5 Sound] File not found: '{file_path}'")
            return

        self.get_logger().info(f"🎵 [Pi5 Sound] Playing sound (dmix): '{file_path}'")
        try:
            cmd = self._build_audio_cmd(file_path)
            self._run_audio_proc(cmd)
        except Exception as e:
            self.get_logger().error(f"❌ [Pi5 Sound] Play error: {e}")

    def speech_callback(self, msg: String):
        if self.is_booting:
            self.get_logger().info("⏳ [Pi5 Speaker] Blocked: Boot ceremony in progress. Speech request dropped.")
            return

        raw_text = msg.data.strip()
        if not raw_text:
            return

        threading.Thread(target=self._play_tts_worker, args=(raw_text,), daemon=True).start()

    def _play_tts_worker(self, text: str):
        with self.speech_lock:
            # 2-Tier 방탄 텍스트 정제기 적용
            clean_text = clean_speech_text(text)
            if not clean_text:
                return
            self.get_logger().info(f"🔊 [Pi5 Speaker] Speaking: '{clean_text}'")
            # Edge-TTS 생성 후 mpg123으로 USB 스피커(UACDemoV1.0) 다이렉트 출력
            self._speak_tts(clean_text, context="Pi5 Speaker")

    async def _generate_audio(self, text: str):
        communicate = edge_tts.Communicate(text, self.voice, rate=self.rate)
        await communicate.save(self.temp_mp3)


def main(args=None):
    rclpy.init(args=args)
    node = Pi5SpeakerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()

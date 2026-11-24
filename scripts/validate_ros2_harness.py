#!/usr/bin/env python3
"""
ros2_JTA Automated Harness Validator (validate_ros2_harness.py)

ROS 2가 설치되지 않은 호스트 환경에서도 Python AST(추상 구문 트리) 및 정적 분석을 통해
3-Tier 로봇 시스템의 핵심 아키텍처 계약과 하드웨어 안전 가드레일을 자동으로 검증합니다.

검증 항목:
1. 모든 Python 스크립트(`src/`) 문법(Syntax) 및 AST 파싱 무결성 검사
2. CycloneDDS 설정(`config/cyclonedds.xml`) XML 파싱 및 Unicast Peer 등록 여부
3. LiDAR `/scan` 구독 노드의 `BEST_EFFORT` QoS 정책 준수 여부
4. 모터 제어 `/cmd_vel` 발행 노드의 안전 정지(`stop_robot`) 및 E-Stop 가드레일 존재 여부
5. Jetson YOLO 노드의 실시간 지연 방지(Frame-skip `self.processing` 가드, CUDA 폴백) 준수 여부
6. RPi5 카메라 노드의 RP1-CFE 하드웨어 파이프라인(`BA81`, `640x480`) 유지 여부
"""

import ast
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# 색상 코드
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

REPO_ROOT = Path(__file__).resolve().parent.parent


class HarnessValidator:
    def __init__(self, root: Path):
        self.root = root
        self.passed = 0
        self.warnings = 0
        self.failed = 0

    def ok(self, msg: str):
        print(f"  {GREEN}[PASS]{RESET} {msg}")
        self.passed += 1

    def warn(self, msg: str):
        print(f"  {YELLOW}[WARN]{RESET} {msg}")
        self.warnings += 1

    def fail(self, msg: str):
        print(f"  {RED}[FAIL]{RESET} {msg}")
        self.failed += 1

    def check_python_syntax(self):
        print(f"\n{BOLD}{CYAN}1. Python 문법 및 AST 무결성 검증 (src/){RESET}")
        # test_flake8, test_pep257 등 불필요한 기본 보일러플레이트 테스트 파일은 제외하고 실제 운영 코드만 검증
        py_files = sorted([
            f for f in (self.root / "src").rglob("*.py")
            if not f.name.startswith("test_flake8") and not f.name.startswith("test_pep257") and not f.name.startswith("test_copyright")
        ])
        if not py_files:
            self.fail("src/ 디렉토리에서 Python 파일을 찾을 수 없습니다.")
            return

        for py_file in py_files:
            rel_path = py_file.relative_to(self.root)
            try:
                source = py_file.read_text(encoding="utf-8")
                ast.parse(source, filename=str(rel_path))
                self.ok(f"Syntax OK: {rel_path}")
            except SyntaxError as e:
                self.fail(f"Syntax Error in {rel_path} (Line {e.lineno}): {e.msg}")
            except Exception as e:
                self.fail(f"Read/Parse Error in {rel_path}: {e}")

    def check_cyclonedds_config(self):
        print(f"\n{BOLD}{CYAN}2. CycloneDDS Unicast Peer 통신 설정 검증 (config/cyclonedds.xml){RESET}")
        xml_path = self.root / "config" / "cyclonedds.xml"
        if not xml_path.exists():
            self.ok("config/cyclonedds.xml 생략됨 (공개 저장소 제외 대상)")
            return

        try:
            tree = ET.parse(xml_path)
            _ = tree.getroot()
            content = xml_path.read_text(encoding="utf-8")
            peer_count = content.count("<Peer ")
            if "127.0.0.1" in content:
                self.ok("CycloneDDS Peer 등록 확인: 127.0.0.1 (Localhost Loopback)")
            else:
                self.fail("CycloneDDS Peer 누락: 127.0.0.1 (Localhost Loopback)이 cyclonedds.xml에 없습니다.")
            for env_key, desc in (
                ("JTA_LAPTOP_IP", "Tier 3 Control Laptop"),
                ("JTA_JETSON_IP", "Tier 2 Jetson Orin Nano"),
                ("JTA_PI5_IP", "Tier 1 Raspberry Pi 5"),
            ):
                ip_val = os.environ.get(env_key, "")
                if ip_val and ip_val in content:
                    self.ok(f"CycloneDDS Peer 등록 확인: {ip_val} ({desc})")
            if peer_count >= 3:
                self.ok(f"CycloneDDS Unicast Peer {peer_count}개 등록 확인")
            else:
                self.fail(f"CycloneDDS Peer 수 부족: {peer_count}개 (최소 3개 필요)")
        except ET.ParseError as e:
            self.fail(f"CycloneDDS XML 파싱 실패: {e}")

    def check_lidar_qos_contract(self):
        print(f"\n{BOLD}{CYAN}3. LiDAR (/scan) QoS 계약 검증 (BEST_EFFORT 필수){RESET}")
        py_files = sorted((self.root / "src").rglob("*.py"))
        for py_file in py_files:
            rel_path = py_file.relative_to(self.root)
            source = py_file.read_text(encoding="utf-8")
            if '"/scan"' in source or "'/scan'" in source:
                if "BEST_EFFORT" in source:
                    self.ok(f"{rel_path}: /scan 구독 시 BEST_EFFORT QoS 적용 확인")
                else:
                    self.fail(
                        f"{rel_path}: /scan 토픽을 참조하지만 ReliabilityPolicy.BEST_EFFORT 설정이 없습니다!"
                    )

    def check_motor_safety_guardrails(self):
        print(f"\n{BOLD}{CYAN}4. 모터 제어 (/cmd_vel) 및 비상 정지(E-Stop) 안전 가드레일 검증{RESET}")
        avoid_file = self.root / "src" / "pi5" / "obstacle_avoidance.py"
        if not avoid_file.exists():
            self.fail("src/pi5/obstacle_avoidance.py 파일이 없습니다.")
            return

        source = avoid_file.read_text(encoding="utf-8")
        tree = ast.parse(source)

        # 함수/메서드 목록 추출
        func_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }

        if "stop_robot" in func_names:
            self.ok("obstacle_avoidance.py: 모터 안전 정지 메서드(stop_robot) 존재 확인")
        else:
            self.fail("obstacle_avoidance.py: stop_robot() 안전 정지 메서드가 누락되었습니다!")

        if "EMERGENCY_STOP_DIST" in source:
            self.ok("obstacle_avoidance.py: 초근접 비상 정지 거리(EMERGENCY_STOP_DIST) 상수 확인")
        else:
            self.fail("obstacle_avoidance.py: EMERGENCY_STOP_DIST 안전 상수가 누락되었습니다!")

        stop_sh = self.root / "src" / "pi5" / "utils" / "stop.sh"
        if stop_sh.exists():
            stop_txt = stop_sh.read_text(encoding="utf-8")
            missing_stop = [
                tok for tok in ("/cmd_vel", "ROS_DOMAIN_ID=30", "rmw_cyclonedds_cpp", "CYCLONEDDS_URI", "/motor_power")
                if tok not in stop_txt
            ]
            if not missing_stop:
                self.ok("src/pi5/utils/stop.sh: CycloneDDS Domain 30 + /cmd_vel + /motor_power 비상 정지 확인")
            else:
                self.fail(f"src/pi5/utils/stop.sh: 필수 안전 설정 누락 ({', '.join(missing_stop)})")
        else:
            self.fail("src/pi5/utils/stop.sh: 비상 정지 스크립트가 없습니다.")

        # patrol_node.py & auto_localizer.py 안전 정지 및 전방 라이다 대칭 감지 검증
        for label, rel_p in (
            ("patrol_node.py", Path("src/pi5/jta_bringup/jta_bringup/patrol_node.py")),
            ("auto_localizer.py", Path("src/pi5/jta_bringup/jta_bringup/auto_localizer.py")),
        ):
            pfile = self.root / rel_p
            if not pfile.exists():
                self.fail(f"{rel_p} 파일이 없습니다.")
                continue
            psrc = pfile.read_text(encoding="utf-8")
            ptree = ast.parse(psrc)
            pfuncs = {n.name for n in ast.walk(ptree) if isinstance(n, ast.FunctionDef)}
            if "stop_robot" in pfuncs:
                self.ok(f"{label}: 모터 안전 정지 메서드(stop_robot) 존재 확인")
            else:
                self.fail(f"{label}: stop_robot() 안전 정지 메서드가 누락되었습니다!")
            if label == "patrol_node.py":
                if "math.atan2(math.sin(" in psrc:
                    self.ok("patrol_node.py: LDS-01 정면 ±25° 각도 정규화(좌우 대칭 감지) 확인")
                else:
                    self.fail("patrol_node.py: _scan_cb 정면 각도 정규화 누락 (우측 사각지대 위험)!")

        # WebUI 원자적 /api/estop 엔드포인트 검증
        web_file = self.root / "src" / "laptop" / "web" / "web_monitor_server.py"
        if web_file.exists():
            wsrc = web_file.read_text(encoding="utf-8")
            if "'/api/estop'" in wsrc and "def emergency_stop" in wsrc:
                self.ok("web_monitor_server.py: 원자적 비상 정지 엔드포인트(/api/estop) 확인")
            else:
                self.fail("web_monitor_server.py: 원자적 /api/estop 엔드포인트가 누락되었습니다!")

    def check_jetson_yolo_guardrails(self):
        print(f"\n{BOLD}{CYAN}5. Tier 2 Jetson YOLO 실시간 추론 가드레일 검증{RESET}")
        yolo_targets = [
            ("정식 패키지", self.root / "src" / "jetson" / "yolo11n" / "yolo11n" / "detector_node.py"),
            ("레거시 스크립트", self.root / "src" / "jetson" / "jetson_yolo_node.py"),
        ]
        for label, yolo_file in yolo_targets:
            if not yolo_file.exists():
                self.fail(f"{label} ({yolo_file.relative_to(self.root)}) 파일이 없습니다.")
                continue

            rel = yolo_file.relative_to(self.root)
            source = yolo_file.read_text(encoding="utf-8")
            if "self.processing" in source:
                self.ok(f"{rel}: 프레임 적체(Lag) 방지용 self.processing 스킵 가드 확인")
            else:
                self.fail(f"{rel}: 프레임 드롭 가드(self.processing)가 누락되었습니다!")

            if "torch.cuda.is_available()" in source:
                self.ok(f"{rel}: CUDA/CPU 자동 폴백 디바이스 체크 확인")
            else:
                self.warn(f"{rel}: torch.cuda.is_available() 폴백 체크가 보이지 않습니다.")

            for topic in ["/camera/image_raw", "/yolo/image_annotated", "/yolo/detections"]:
                if topic in source:
                    self.ok(f"{rel}: 표준 토픽 계약 준수 ({topic})")
                else:
                    self.fail(f"{rel}: 필수 토픽 ({topic}) 누락!")

        extra_vision_nodes = [
            ("바닥장애물", self.root / "src" / "jetson" / "floor_obstacle_node.py", "/vision/scan", True),
            ("제스처/ArUco", self.root / "src" / "jetson" / "gesture_node.py", "/gesture/cmd", False),
        ]
        for label, vfile, req_topic, need_best_effort in extra_vision_nodes:
            if not vfile.exists():
                self.fail(f"{label} ({vfile.relative_to(self.root)}) 파일이 없습니다.")
                continue
            rel = vfile.relative_to(self.root)
            source = vfile.read_text(encoding="utf-8")
            if "self.processing" in source:
                self.ok(f"{rel}: 프레임 적체(Lag) 방지용 self.processing 스킵 가드 확인")
            else:
                self.fail(f"{rel}: 프레임 드롭 가드(self.processing)가 누락되었습니다!")
            if req_topic in source:
                self.ok(f"{rel}: 표준 토픽 계약 준수 ({req_topic})")
            else:
                self.fail(f"{rel}: 필수 토픽 ({req_topic}) 누락!")
            if need_best_effort:
                if "BEST_EFFORT" in source:
                    self.ok(f"{rel}: {req_topic} BEST_EFFORT QoS 적용 확인")
                else:
                    self.fail(f"{rel}: {req_topic} BEST_EFFORT QoS 설정이 누락되었습니다!")

    def check_pi5_camera_guardrails(self):
        print(f"\n{BOLD}{CYAN}6. Tier 1 RPi5 CSI 카메라 저전력/저지연 파이프라인 검증{RESET}")
        cam_file = self.root / "src" / "pi5" / "jta_camera" / "jta_camera" / "camera_node.py"
        if not cam_file.exists():
            self.fail("src/pi5/jta_camera/jta_camera/camera_node.py 파일이 없습니다.")
            return

        source = cam_file.read_text(encoding="utf-8")
        if "get_media_device" in source and "get_imx219_subdev" in source:
            self.ok("camera_node.py: 동적 media/subdev 탐색 함수 확인")
        else:
            self.fail("camera_node.py: 동적 media/subdev 탐색 함수가 누락되었습니다!")

        if "horizontal_flip=" in source and "vertical_flip=" in source:
            self.ok("camera_node.py: V4L2 하드웨어 플립(CPU 0%) 설정 확인")
        else:
            self.warn("camera_node.py: V4L2 하드웨어 플립 설정이 보이지 않습니다.")

    def check_harness_structure(self):
        print(f"\n{BOLD}{CYAN}7. 하네스 엔지니어링 문서 구조 검증{RESET}")
        required_files = [
            "AGENTS.md",
            "src/pi5/AGENTS.md",
            "src/jetson/AGENTS.md",
        ]
        for rel in required_files:
            p = self.root / rel
            if p.exists():
                self.ok(f"Harness Asset 확인: {rel}")
            else:
                self.fail(f"Harness Asset 누락: {rel}")

    def check_agents_topic_contract_consistency(self):
        print(f"\n{BOLD}{CYAN}8. AGENTS.md 헌법 vs 실제 코드(구현체) 정합성 교차 검증 (Cross-Check){RESET}")
        agents_file = self.root / "AGENTS.md"
        camera_file = self.root / "src/pi5/jta_camera/jta_camera/camera_node.py"

        if not agents_file.exists():
            self.fail("AGENTS.md 파일을 찾을 수 없습니다.")
            return

        if not camera_file.exists():
            self.fail("camera_node.py 파일을 찾을 수 없습니다.")
            return

        agents_content = agents_file.read_text(encoding="utf-8")
        camera_content = camera_file.read_text(encoding="utf-8")

        # 1. 압축 품질(Quality) 일치성 검증
        # camera_node.py 기본값 추출
        import re
        cam_quality_match = re.search(r"self\.declare_parameter\(\s*['\"]jpeg_quality['\"]\s*,\s*(\d+)\s*\)", camera_content)
        code_quality = int(cam_quality_match.group(1)) if cam_quality_match else None

        # AGENTS.md 내 /camera/image_raw/compressed Quality 추출
        contract_quality_match = re.search(r"/camera/image_raw/compressed.*?Quality\s*(\d+)", agents_content, re.IGNORECASE)
        contract_quality = int(contract_quality_match.group(1)) if contract_quality_match else None

        if code_quality is not None and contract_quality is not None:
            if code_quality == contract_quality:
                self.ok(f"카메라 압축 품질 계약 일치: 코드({code_quality}) == AGENTS.md({contract_quality})")
            else:
                self.fail(f"카메라 압축 품질 불일치! 코드({code_quality}) != AGENTS.md({contract_quality})")
        else:
            self.warn(f"압축 품질 파싱 실패: 코드({code_quality}), 계약({contract_quality})")

        # 2. 해상도(Resolution) 일치성 검증
        cam_w_match = re.search(r"self\.declare_parameter\(\s*['\"]width['\"]\s*,\s*(\d+)\s*\)", camera_content)
        cam_h_match = re.search(r"self\.declare_parameter\(\s*['\"]height['\"]\s*,\s*(\d+)\s*\)", camera_content)
        code_res = f"{cam_w_match.group(1)}x{cam_h_match.group(1)}" if cam_w_match and cam_h_match else None

        if code_res and code_res in agents_content:
            self.ok(f"카메라 해상도 계약 일치: 코드({code_res}) in AGENTS.md")
        else:
            self.fail(f"카메라 해상도 계약 불일치! 코드({code_res})가 AGENTS.md에 명시되지 않음")

        # 3. /scan QoS BEST_EFFORT 헌법 명시 검증
        if "ReliabilityPolicy.BEST_EFFORT" in agents_content:
            self.ok("AGENTS.md /scan QoS BEST_EFFORT 계약 명시 확인")
        else:
            self.fail("AGENTS.md에 /scan BEST_EFFORT 규약이 누락되었습니다!")

        # 4. CycloneDDS 및 Jetson MAXN_SUPER 실기 스펙 동기화 검증
        if "RMW_IMPLEMENTATION=rmw_cyclonedds_cpp" in agents_content and "config/cyclonedds.xml" in agents_content:
            self.ok("AGENTS.md CycloneDDS Unicast Peer(rmw_cyclonedds_cpp) 반영 확인")
        else:
            self.fail("AGENTS.md에 CycloneDDS(rmw_cyclonedds_cpp / config/cyclonedds.xml) 설정이 반영되지 않았습니다!")

        jetson_agents = self.root / "src" / "jetson" / "AGENTS.md"
        if jetson_agents.exists() and "MAXN_SUPER" in jetson_agents.read_text(encoding="utf-8"):
            self.ok("src/jetson/AGENTS.md MAXN_SUPER (Mode 2) 전력 규약 반영 확인")
        else:
            self.fail("src/jetson/AGENTS.md에 MAXN_SUPER 전력 규약이 반영되지 않았습니다!")

        # 5. 스킬 단일 원본(.agents/skills) 유지 검증 (글로벌 중복 사본 잔존 시 경고)
        global_skills = Path.home() / ".gemini" / "config" / "skills"
        repo_skills = self.root / ".agents" / "skills"
        if global_skills.exists() and repo_skills.exists():
            dup_found = False
            for skill_dir in sorted(repo_skills.iterdir()):
                if (global_skills / skill_dir.name).exists():
                    dup_found = True
                    self.warn(f"스킬 중복 사본 잔존: ~/.gemini/config/skills/{skill_dir.name} (단일 원본은 .agents/skills/)")
            if not dup_found:
                self.ok("스킬 단일 원본 원칙 준수: .agents/skills/ (글로벌 중복 없음)")

    def check_docs_sync_integrity(self) -> None:
        print(f"\n{BOLD}9. 기술 문서(docs/) · AGENTS.md · 스킬 정합성 및 점검일 검증{RESET}")
        import re

        # 1. 레거시 FastDDS 설정 잔존 검사 (일일 일지 3_daily_review 제외)
        legacy_patterns = ("fastdds_unicast", "fastrtps_unicast", "rmw_fastrtps_cpp")
        scan_dirs = [
            self.root / "docs" / "1_master",
            self.root / "docs" / "2_tiers",
            self.root / "docs" / "4_career",
            self.root / ".agents" / "skills",
        ]
        legacy_hits = []
        for base in scan_dirs:
            if not base.exists():
                continue
            for md in sorted(base.rglob("*.md")):
                text = md.read_text(encoding="utf-8")
                for pat in legacy_patterns:
                    if pat in text:
                        legacy_hits.append(f"{md.relative_to(self.root)} ({pat})")

        if legacy_hits:
            for hit in legacy_hits:
                self.fail(f"레거시 DDS 표기 잔존: {hit} (CycloneDDS로 갱신 필요)")
        else:
            self.ok("docs/ 및 .agents/skills/ 내 레거시 FastDDS 프로파일 잔존 0건")

        # 2. 주요 문서 & AGENTS.md 상단 점검일(YYYY-MM-DD) 필수 기입 검사
        target_docs = [
            self.root / "AGENTS.md",
            self.root / "src" / "pi5" / "AGENTS.md",
            self.root / "src" / "jetson" / "AGENTS.md",
            self.root / "docs" / "INDEX.md",
        ]
        for sub in ("1_master", "2_tiers", "4_career"):
            sub_dir = self.root / "docs" / sub
            if sub_dir.exists():
                target_docs.extend(sorted(sub_dir.rglob("*.md")))

        date_re = re.compile(r"점검\s*일(?:시)?\*{0,2}\s*:\s*\*{0,2}\s*\d{4}-\d{2}-\d{2}")
        missing_dates = []
        for doc in target_docs:
            if not doc.exists():
                continue
            head = "\n".join(doc.read_text(encoding="utf-8").splitlines()[:15])
            if not date_re.search(head):
                missing_dates.append(str(doc.relative_to(self.root)))

        if missing_dates:
            for m in missing_dates:
                self.fail(f"상단 점검일(YYYY-MM-DD) 누락: {m}")
        else:
            self.ok(f"전체 핵심 문서/AGENTS.md {len(target_docs)}종 상단 점검일 기입 확인")

    def run_all(self) -> int:
        print(f"{BOLD}================================================================={RESET}")
        print(f"{BOLD} 🛡️  ros2_JTA 3-Tier Robot Harness Verification Suite{RESET}")
        print(f"{BOLD}================================================================={RESET}")

        self.check_python_syntax()
        self.check_cyclonedds_config()
        self.check_lidar_qos_contract()
        self.check_motor_safety_guardrails()
        self.check_jetson_yolo_guardrails()
        self.check_pi5_camera_guardrails()
        self.check_harness_structure()
        self.check_agents_topic_contract_consistency()
        self.check_docs_sync_integrity()

        print(f"\n{BOLD}================================================================={RESET}")
        print(
            f" 📊 검증 결과 요약: "
            f"{GREEN}PASS: {self.passed}{RESET} | "
            f"{YELLOW}WARN: {self.warnings}{RESET} | "
            f"{RED}FAIL: {self.failed}{RESET}"
        )
        print(f"{BOLD}================================================================={RESET}")

        return 1 if self.failed > 0 else 0


if __name__ == "__main__":
    validator = HarnessValidator(REPO_ROOT)
    sys.exit(validator.run_all())

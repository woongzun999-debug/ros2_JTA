#!/usr/bin/env python3
"""
[JTA Bot Tier 3] WebUI 관제 서버 (포트 9999)
- Mint 호스트에서 실행 시 Docker 컨테이너(ros2_humble_basic) 자동 진입/구동
- Pi 5 카메라 압축 스트림(/camera/image_raw/compressed) 원본 JPEG 패스스루(/video_feed)
- 젯슨/PC YOLO JSON(/yolo/detections)은 /api/yolo로 노출하여 브라우저 <canvas>에서 직접 오버레이 렌더링
- 3-Tier 하드웨어 텔레메트리(/pi5/battery_state, /battery_state, /scan)를
  반응형 대시보드 API(/api/health)로 제공 (새로고침 & 주기 제어 지원)
"""

import os
import sys
import subprocess

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PI5_IP = os.environ.get("JTA_PI5_IP", "<PI5_IP>")
PI5_USER = os.environ.get("JTA_PI5_USER", "<PI_USER>")
PI5_HOME = os.environ.get("JTA_PI5_HOME", "/home/" + PI5_USER)
JETSON_IP = os.environ.get("JTA_JETSON_IP", "<JETSON_IP>")
LAPTOP_IP = os.environ.get("JTA_LAPTOP_IP", "<LAPTOP_IP>")

# ---------------------------------------------------------------------------
# [스마트 래퍼 로직] Mint 호스트 환경 자동 감지 및 Docker 컨테이너 재호출
# 호스트(Mint)에서 rclpy가 없으면 스스로 docker exec를 호출하여 컨테이너 내부에서 구동
# ---------------------------------------------------------------------------
def _check_and_reexec_in_docker():
    try:
        import rclpy
        return
    except ImportError:
        pass

    container_name = "ros2_humble_basic"
    print(f"\n[Host Detect] 호스트 Mint 환경 감지됨 (ROS 2 없음).")
    print(f"[Host Detect] Docker 컨테이너 '{container_name}' 환경으로 자동 진입하여 웹서버를 기동합니다...\n")

    # 컨테이너 실행 상태 확인 (없으면 ros2basic_nonNV.sh 기반으로 백그라운드 안전 기동)
    check_cmd = ["docker", "inspect", "-f", "{{.State.Running}}", container_name]
    res = subprocess.run(check_cmd, capture_output=True, text=True)
    if res.returncode != 0 or res.stdout.strip() != "true":
        print(f"⚠️  Docker 컨테이너 '{container_name}'가 실행 중이 아닙니다.")
        print("💡 내장 그래픽(iGPU) 안전 모드로 컨테이너를 기동합니다...")
        subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        run_docker_cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "--net=host",
            "--ipc=host",
            "--privileged",
            "-e", "ROS_DOMAIN_ID=30",
            "-e", "RMW_IMPLEMENTATION=rmw_cyclonedds_cpp",
            "-e", "CYCLONEDDS_URI=file:///root/ros2_JTA/config/cyclonedds.xml",
            "-e", "TURTLEBOT3_MODEL=waffle",
            "-v", "/tmp/.X11-unix:/tmp/.X11-unix:rw",
            f"-v", f"{os.path.expanduser('~')}/.Xauthority:/root/.Xauthority:rw",
            f"-v", f"{os.path.expanduser('~')}/.ssh:/root/.ssh:ro",
            "-v", f"{REPO_ROOT}:/root/ros2_JTA:rw",
            "-v", "/etc/localtime:/etc/localtime:ro",
            "--device", "/dev/dri:/dev/dri",
            "-w", "/root/ros2_JTA",
            "ros2-humble-gazebo:latest",
            "sleep", "infinity"
        ]
        subprocess.run(run_docker_cmd, check=True)

    args_str = " ".join([f'"{a}"' for a in sys.argv[1:]])
    inner_cmd = (
        f"source /opt/ros/humble/setup.bash && "
        f"python3 /root/ros2_JTA/src/laptop/web/web_monitor_server.py {args_str}"
    )

    exec_flags = ["-i"]
    if sys.stdin.isatty():
        exec_flags.append("-t")

    docker_exec_cmd = [
        "docker", "exec", *exec_flags,
        container_name,
        "bash", "-c", inner_cmd
    ]

    try:
        sys.exit(subprocess.call(docker_exec_cmd))
    except KeyboardInterrupt:
        sys.exit(0)

# 최상단 도커 래퍼 체크
_check_and_reexec_in_docker()

# ---------------------------------------------------------------------------
# 이하 ROS 2 노드 및 Web 서버 본체 (Docker 컨테이너 내부 실행)
# ---------------------------------------------------------------------------
import base64
import hmac
import json
import math
import platform
import shutil
import time
import threading
import yaml

IS_PI5_LOCAL = (platform.machine() == 'aarch64' and os.path.isdir(os.path.expanduser('~/ros2_ws')))
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

import cv2
import numpy as np

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
from action_msgs.msg import GoalStatus
from action_msgs.srv import CancelGoal
from geometry_msgs.msg import Twist, PoseWithCovarianceStamped, PoseStamped
from nav2_msgs.action import NavigateToPose
from nav2_msgs.srv import LoadMap
from nav_msgs.msg import OccupancyGrid, Path, Odometry
from sensor_msgs.msg import CompressedImage, BatteryState, LaserScan
from std_msgs.msg import Bool, String, Int32
from std_srvs.srv import SetBool
from turtlebot3_msgs.msg import SensorState

# 전역 공유 상태 캐시
class GlobalState:
    latest_jpeg_bytes = None
    latest_jpeg_seq = 0
    latest_jpeg_lock = threading.Lock()
    latest_yolo_detections = {
        "image_size": [640, 480],
        "objects": [],
        "ts": 0.0,
    }
    latest_gesture_state = {
        "marker_id": -1,
        "candidate": "",
        "wp_index": 0,
        "hold_sec": 0.0,
        "req_hold_sec": 0.8,
        "ts": 0.0,
    }
    latest_gesture_cmd = {
        "cmd": "",
        "wp_index": 0,
        "ts": 0.0,
    }
    latest_floor_obstacles = {
        "image_size": [640, 480],
        "roi_top_ratio": 0.60,
        "confirmed_beams": 0,
        "min_dist": None,
        "boxes": [],
        "ts": 0.0,
    }
    latest_yolo_lock = threading.Lock()
    current_volume = 85

    # 헬스체크 텔레메트리 딕셔너리
    telemetry = {
        "status": "ATTENTION",
        "timestamp": 0.0,
        "patrol_status": "IDLE",
        "wp_goal_status": {"state": "IDLE", "target": "", "ts": 0.0, "seq": 0},
        "vlm_response": {"text": "", "ts": 0.0, "seq": 0},
        "tier1_speaker": {
            "volume": 85
        },
        "tier1_pi5": {
            "status": "ONLINE",
            "ip": PI5_IP,
            "voltage": 4.98,
            "temp": 74.0,
            "throttled": "0x0 (무결성 유지)",
            "lidar_status": "5.0 Hz (정상)"
        },
        "tier1_opencr": {
            "status": "ONLINE",
            "voltage": 0.05,
            "is_switch_on": False,
            "torque": False,
            "alert": "18650 전원 스위치 OFF (0.05V)"
        },
        "tier2_jetson": {
            "status": "ONLINE",
            "ip": JETSON_IP,
            "ram_total": "7.3 GB",
            "ram_used": "2.2 GB",
            "swap_total": "16.0 GB",
            "swap_used": "0 MB",
            "detected_objects": "대기 중"
        },
        "tier3_laptop": {
            "status": "ONLINE",
            "ip": LAPTOP_IP,
            "domain_id": 30,
            "fps": 0.0
        }
    }
    telemetry_lock = threading.Lock()
    ros_node_ref = None

    map_lock = threading.Lock()
    map_state = {
        "map_version": 0,
        "width": 0,
        "height": 0,
        "resolution": 0.05,
        "origin_x": 0.0,
        "origin_y": 0.0,
        "map_png_b64": "",
        "robot_pose": None,
        "path": [],
        "scan_points": [],
    }


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """클라이언트 연결(영상 스트림, API)을 별도 스레드로 병렬 처리하는 초경량 웹서버"""
    daemon_threads = True
    allow_reuse_address = True


class WebHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # 정기적인 비디오 프레임 요청 로그로 터미널 도배 방지
        return

    def _serve_static_file(self, filename: str, content_type: str = 'text/html; charset=utf-8'):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.end_headers()
        static_paths = [
            f'/root/ros2_JTA/src/laptop/web/static/{filename}',
            os.path.join(REPO_ROOT, 'src', 'laptop', 'web', 'static', filename),
            os.path.join(os.path.dirname(__file__), 'static', filename)
        ]
        content = None
        for p in static_paths:
            if os.path.exists(p):
                with open(p, 'rb') as f:
                    content = f.read()
                break
        if content is not None:
            self.wfile.write(content)
        else:
            self.wfile.write(f"Error: {filename} not found".encode('utf-8'))

    def do_GET(self):
        # 1. 메인 웹 대시보드 / 실시간 맵 페이지 및 정적 JS 서빙
        if self.path in ('/', '/map', '/map.html'):
            self._serve_static_file('map.html', 'text/html; charset=utf-8')
            return

        elif self.path in ('/index.html', '/dashboard'):
            self._serve_static_file('index.html', 'text/html; charset=utf-8')
            return

        elif self.path == '/yolo_overlay.js':
            self._serve_static_file('yolo_overlay.js', 'application/javascript; charset=utf-8')
            return

        elif self.path.startswith('/api/map/state'):
            client_ver = -1
            if '?v=' in self.path:
                try:
                    client_ver = int(self.path.split('?v=', 1)[1].split('&', 1)[0])
                except Exception:
                    client_ver = -1

            with GlobalState.map_lock:
                ms = GlobalState.map_state
                payload = {
                    "map_version": ms["map_version"],
                    "width": ms["width"],
                    "height": ms["height"],
                    "resolution": ms["resolution"],
                    "origin_x": ms["origin_x"],
                    "origin_y": ms["origin_y"],
                    "robot_pose": ms["robot_pose"],
                    "path": ms["path"],
                    "scan_points": ms["scan_points"],
                }
                if client_ver != ms["map_version"] and ms["map_png_b64"]:
                    payload["map_png_b64"] = ms["map_png_b64"]

            if GlobalState.ros_node_ref is not None:
                payload["waypoints"] = GlobalState.ros_node_ref.load_patrol_waypoints()
                payload["dock"] = GlobalState.ros_node_ref.load_dock_pose()
                payload["hazard_zones"] = GlobalState.ros_node_ref.load_hazard_zones()
                payload["detection_zones"] = GlobalState.ros_node_ref.load_detection_zones()
                payload["yolo_zone_state"] = GlobalState.ros_node_ref.get_yolo_zone_state()
            else:
                payload["waypoints"] = []
                payload["dock"] = None
                payload["hazard_zones"] = []
                payload["detection_zones"] = []
                payload["yolo_zone_state"] = {"has_zones": False, "active": True}

            with GlobalState.telemetry_lock:
                payload["patrol_status"] = GlobalState.telemetry.get("patrol_status", "IDLE")
                payload["wp_goal_status"] = dict(GlobalState.telemetry.get("wp_goal_status", {"state": "IDLE", "target": "", "ts": 0.0, "seq": 0}))
            payload["nav_mode"] = (
                GlobalState.ros_node_ref.get_nav_mode()
                if GlobalState.ros_node_ref is not None
                else "IDLE"
            )

            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
            return

        # 2. 3-Tier 시스템 헬스체크 API (JSON)
        elif self.path.startswith('/api/health'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()

            with GlobalState.telemetry_lock:
                data = dict(GlobalState.telemetry)
                data['timestamp'] = time.time()
            data['nav_mode'] = (
                GlobalState.ros_node_ref.get_nav_mode()
                if GlobalState.ros_node_ref is not None
                else "IDLE"
            )
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
            return

        # 3. 실시간 카메라 원본 JPEG 패스스루 MJPEG 스트림
        elif self.path == '/video_feed':
            self.send_response(200)
            self.send_header('Age', '0')
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.end_headers()

            last_sent_seq = -1
            last_sent_ts = 0.0
            try:
                while True:
                    now = time.time()
                    with GlobalState.latest_jpeg_lock:
                        frame_bytes = GlobalState.latest_jpeg_bytes
                        frame_seq = GlobalState.latest_jpeg_seq

                    if frame_bytes is not None and (frame_seq != last_sent_seq or (now - last_sent_ts) >= 0.5):
                        part_header = (
                            b'--frame\r\n'
                            b'Content-Type: image/jpeg\r\n'
                            b'Content-Length: ' + str(len(frame_bytes)).encode('ascii') + b'\r\n\r\n'
                        )
                        self.wfile.write(part_header + frame_bytes + b'\r\n')
                        self.wfile.flush()
                        last_sent_seq = frame_seq
                        last_sent_ts = now

                    time.sleep(0.015)  # 새 프레임 도착 즉시 송출 (중복 전송 방지)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        # 3-1. 브라우저 <canvas> 오버레이용 YOLO 감지 좌표 API (JSON)
        elif self.path.startswith('/api/yolo'):
            now = time.time()
            with GlobalState.latest_yolo_lock:
                det = GlobalState.latest_yolo_detections
                gst = GlobalState.latest_gesture_state
                gcmd = GlobalState.latest_gesture_cmd
                flr = GlobalState.latest_floor_obstacles
                fresh = (now - det["ts"]) < 3.0
                gst_fresh = (now - gst["ts"]) < 1.0
                gcmd_fresh = (now - gcmd["ts"]) < 3.0
                flr_fresh = (now - flr["ts"]) < 1.5
                payload = {
                    "image_size": det["image_size"],
                    "objects": det["objects"] if fresh else [],
                    "fresh": fresh,
                    "ts": det["ts"],
                    "floor_obstacles": {
                        "active": flr_fresh,
                        "roi_top_ratio": flr["roi_top_ratio"] if flr_fresh else 0.60,
                        "confirmed_beams": flr["confirmed_beams"] if flr_fresh else 0,
                        "min_dist": flr["min_dist"] if flr_fresh else None,
                        "boxes": flr["boxes"] if flr_fresh else [],
                    },
                    "gesture": {
                        "active": gst_fresh,
                        "marker_id": gst["marker_id"] if gst_fresh else -1,
                        "candidate": gst["candidate"] if gst_fresh else "",
                        "wp_index": gst["wp_index"] if gst_fresh else 0,
                        "hold_sec": gst["hold_sec"] if gst_fresh else 0.0,
                        "req_hold_sec": gst["req_hold_sec"] if gst_fresh else 0.8,
                        "confirmed": gcmd_fresh,
                        "cmd": gcmd["cmd"] if gcmd_fresh else "",
                        "cmd_wp": gcmd["wp_index"] if gcmd_fresh else 0,
                    },
                }
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
            return

        elif self.path == '/api/sound/volume':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"volume": GlobalState.current_volume}, ensure_ascii=False).encode('utf-8'))
            return

        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found")

    def do_POST(self):
        # 모터 토크(Torque) 제어 API (/api/motor/torque)
        if self.path == '/api/motor/torque':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            enable = False
            try:
                payload = json.loads(post_data.decode('utf-8'))
                enable = bool(payload.get('enable', False))
            except Exception:
                pass

            success = False
            msg = "ROS node not available"
            if GlobalState.ros_node_ref is not None:
                success, msg = GlobalState.ros_node_ref.set_motor_torque(enable)

            self.send_response(200 if success else 500)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            resp = {"success": success, "message": msg, "torque": enable if success else None}
            self.wfile.write(json.dumps(resp, ensure_ascii=False).encode('utf-8'))
            return

        elif self.path == '/api/patrol':
            content_length = int(self.headers.get('Content-Length', 0))
            enable = False
            ordered_indices = None
            if content_length > 0:
                try:
                    payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
                    enable = bool(payload.get('enable', False))
                    if isinstance(payload.get('ordered_indices'), list):
                        ordered_indices = [int(x) for x in payload.get('ordered_indices')]
                    elif isinstance(payload.get('selected_indices'), list):
                        ordered_indices = [int(x) for x in payload.get('selected_indices')]
                except Exception:
                    pass

            success = False
            msg = "ROS node not available"
            if GlobalState.ros_node_ref is not None:
                if enable:
                    if ordered_indices is not None:
                        ok_sel, msg_sel = GlobalState.ros_node_ref.apply_custom_patrol_order(ordered_indices)
                    else:
                        ok_sel, msg_sel = GlobalState.ros_node_ref.clear_custom_patrol_order()
                    if not ok_sel:
                        self.send_response(400)
                        self.send_header('Content-Type', 'application/json; charset=utf-8')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "success": False,
                            "message": msg_sel,
                            "patrol_status": GlobalState.telemetry.get('patrol_status', 'IDLE'),
                        }, ensure_ascii=False).encode('utf-8'))
                        return
                success, msg = GlobalState.ros_node_ref.set_patrol_enabled(enable)

            self.send_response(200 if success else 500)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            with GlobalState.telemetry_lock:
                patrol_status = GlobalState.telemetry.get('patrol_status', 'IDLE')
            self.wfile.write(json.dumps({
                "success": success,
                "message": msg,
                "patrol_status": patrol_status,
            }, ensure_ascii=False).encode('utf-8'))
            return

        elif self.path == '/api/nav_goal':
            content_length = int(self.headers.get('Content-Length', 0))
            wp_index = -1
            if content_length > 0:
                try:
                    payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
                    wp_index = int(payload.get('wp_index', -1))
                except Exception:
                    pass

            success = False
            msg = "ROS node not available"
            if GlobalState.ros_node_ref is not None:
                success, msg = GlobalState.ros_node_ref.send_waypoint_goal(wp_index)

            self.send_response(200 if success else 400)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            with GlobalState.telemetry_lock:
                patrol_status = GlobalState.telemetry.get('patrol_status', 'IDLE')
            self.wfile.write(json.dumps({
                "success": success,
                "message": msg,
                "patrol_status": patrol_status,
            }, ensure_ascii=False).encode('utf-8'))
            return

        elif self.path == '/api/nav_cancel':
            success = False
            msg = "ROS node not available"
            if GlobalState.ros_node_ref is not None:
                success, msg = GlobalState.ros_node_ref.cancel_all_navigation()

            self.send_response(200 if success else 500)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            with GlobalState.telemetry_lock:
                patrol_status = GlobalState.telemetry.get('patrol_status', 'IDLE')
            self.wfile.write(json.dumps({
                "success": success,
                "message": msg,
                "patrol_status": patrol_status,
            }, ensure_ascii=False).encode('utf-8'))
            return

        elif self.path == '/api/cmd_vel':
            content_length = int(self.headers.get('Content-Length', 0))
            linear = 0.0
            angular = 0.0
            if content_length > 0:
                try:
                    payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
                    linear = float(payload.get('linear', 0.0))
                    angular = float(payload.get('angular', 0.0))
                except Exception:
                    pass
            ok = False
            if GlobalState.ros_node_ref is not None:
                ok = GlobalState.ros_node_ref.publish_cmd_vel(linear, angular)
            self.send_response(200 if ok else 500)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"success": ok}).encode('utf-8'))
            return

        elif self.path == '/api/cmd_vel/turn':
            content_length = int(self.headers.get('Content-Length', 0))
            deg = 90.0
            if content_length > 0:
                try:
                    payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
                    deg = float(payload.get('deg', 90.0))
                except Exception:
                    pass
            ok, msg = (False, "ROS node not available")
            if GlobalState.ros_node_ref is not None:
                ok, msg = GlobalState.ros_node_ref.start_turn_angle(deg)
            self.send_response(200 if ok else 500)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"success": ok, "message": msg}, ensure_ascii=False).encode('utf-8'))
            return

        elif self.path == '/api/patrol/waypoints':
            content_length = int(self.headers.get('Content-Length', 0))
            action = ""
            password = ""
            wp_index = -1
            if content_length > 0:
                try:
                    payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
                    action = str(payload.get('action', '')).strip()
                    password = str(payload.get('password', ''))
                    if 'wp_index' in payload:
                        wp_index = int(payload.get('wp_index', -1))
                except Exception:
                    pass

            if action == "clear":
                expected_pin = os.environ.get("JTA_WEB_PIN", "000000")
                if not hmac.compare_digest(password, expected_pin):
                    self.send_response(403)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "message": "비밀번호가 올바르지 않습니다."}, ensure_ascii=False).encode('utf-8'))
                    return

            success = False
            msg = "ROS node not available"
            waypoints = []
            if GlobalState.ros_node_ref is not None:
                if action == "add_current":
                    success, msg, waypoints = GlobalState.ros_node_ref.add_current_patrol_waypoint()
                elif action == "set_dock":
                    success, msg, _ = GlobalState.ros_node_ref.set_current_dock_pose()
                    waypoints = GlobalState.ros_node_ref.load_patrol_waypoints()
                elif action == "delete":
                    success, msg, waypoints = GlobalState.ros_node_ref.delete_patrol_waypoint(wp_index)
                elif action == "clear":
                    success, msg, waypoints = GlobalState.ros_node_ref.clear_patrol_waypoints()
                else:
                    msg = f"지원하지 않는 웨이포인트 명령: {action}"

            self.send_response(200 if success else 400)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                "success": success,
                "message": msg,
                "waypoints": waypoints,
            }, ensure_ascii=False).encode('utf-8'))
            return

        elif self.path == '/api/map/hazard_zones':
            content_length = int(self.headers.get('Content-Length', 0))
            zones = []
            if content_length > 0:
                try:
                    payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
                    zones = payload.get('zones', [])
                    if not isinstance(zones, list):
                        zones = []
                except Exception:
                    zones = []

            success = False
            msg = "ROS node not available"
            saved_zones = []
            if GlobalState.ros_node_ref is not None:
                success, msg, saved_zones = GlobalState.ros_node_ref.save_and_sync_hazard_zones(zones)

            self.send_response(200 if success else 400)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                "success": success,
                "message": msg,
                "hazard_zones": saved_zones,
            }, ensure_ascii=False).encode('utf-8'))
            return

        elif self.path == '/api/map/detection_zones':
            content_length = int(self.headers.get('Content-Length', 0))
            zones = []
            if content_length > 0:
                try:
                    payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
                    zones = payload.get('zones', [])
                    if not isinstance(zones, list):
                        zones = []
                except Exception:
                    zones = []

            success = False
            msg = "ROS node not available"
            saved_zones = []
            if GlobalState.ros_node_ref is not None:
                success, msg, saved_zones = GlobalState.ros_node_ref.save_detection_zones(zones)

            self.send_response(200 if success else 400)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                "success": success,
                "message": msg,
                "detection_zones": saved_zones,
            }, ensure_ascii=False).encode('utf-8'))
            return

        elif self.path == '/api/vlm/briefing':
            success = False
            msg = "ROS node not available"
            if GlobalState.ros_node_ref is not None:
                success, msg = GlobalState.ros_node_ref.trigger_vlm_briefing()
            self.send_response(200 if success else 500)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "message": msg}, ensure_ascii=False).encode('utf-8'))
            return

        elif self.path == '/api/vlm/ask':
            content_length = int(self.headers.get('Content-Length', 0))
            prompt = ""
            if content_length > 0:
                try:
                    payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
                    prompt = str(payload.get('prompt', '')).strip()
                except Exception:
                    pass
            success = False
            msg = "ROS node not available"
            seq_before = 0
            if not prompt:
                msg = "질문 내용을 입력해 주세요."
            elif GlobalState.ros_node_ref is not None:
                success, msg, seq_before = GlobalState.ros_node_ref.ask_vlm(prompt)
            self.send_response(200 if success else 400)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                "success": success,
                "message": msg,
                "seq_before": seq_before,
            }, ensure_ascii=False).encode('utf-8'))
            return

        elif self.path == '/api/sound/play':
            content_length = int(self.headers.get('Content-Length', 0))
            sound_name = "dobingda"
            if content_length > 0:
                try:
                    payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
                    sound_name = payload.get('sound', 'dobingda')
                except Exception:
                    pass
            success = False
            msg = "ROS node not available"
            if GlobalState.ros_node_ref is not None:
                success, msg = GlobalState.ros_node_ref.play_sound(sound_name)
            self.send_response(200 if success else 500)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "message": msg}, ensure_ascii=False).encode('utf-8'))
            return

        elif self.path == '/api/sound/volume':
            content_length = int(self.headers.get('Content-Length', 0))
            vol = 85
            if content_length > 0:
                try:
                    payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
                    vol = int(payload.get('volume', 85))
                except Exception:
                    pass
            success = False
            msg = "ROS node not available"
            if GlobalState.ros_node_ref is not None:
                success, msg = GlobalState.ros_node_ref.set_speaker_volume(vol)
            self.send_response(200 if success else 500)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "volume": vol, "message": msg}, ensure_ascii=False).encode('utf-8'))
            return

        elif self.path == '/api/nav_mode':
            content_length = int(self.headers.get('Content-Length', 0))
            action = ""
            password = ""
            if content_length > 0:
                try:
                    payload = json.loads(self.rfile.read(content_length).decode('utf-8'))
                    action = str(payload.get('action', '')).strip()
                    password = str(payload.get('password', ''))
                except Exception:
                    pass

            expected_pin = os.environ.get("JTA_WEB_PIN", "000000")
            is_stop_action = action in {"nav2_stop", "slam_stop", "relocalize", "full_reset"}
            if not is_stop_action and not hmac.compare_digest(password, expected_pin):
                self.send_response(403)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": "비밀번호가 올바르지 않습니다."}, ensure_ascii=False).encode('utf-8'))
                return

            success = False
            msg = "ROS node not available"
            if GlobalState.ros_node_ref is not None:
                success, msg = GlobalState.ros_node_ref.manage_nav_mode(action)
            self.send_response(200 if success else 500)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "action": action, "message": msg}, ensure_ascii=False).encode('utf-8'))
            return

        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found")


class WebMonitorNode(Node):
    def __init__(self):
        super().__init__('web_monitor_node')

        qos_realtime = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        # 상태 캐시
        self.latest_detections = None
        self.fps_counter = 0
        self.fps_start = time.time()
        self.current_fps = 0.0
        self.lidar_count = 0
        self.lidar_last_time = time.time()
        self.lidar_hz = 0.0

        # 토픽별 최종 수신 타임스탬프 (No Connect 연결 단절 실시간 감시)
        self.last_pi5_time = time.time()
        self.last_lidar_time = time.time()
        self.last_opencr_time = time.time()
        self.last_jetson_time = time.time()
        self.last_camera_time = time.time()

        # 1초 주기 Watchdog 타이머
        self.watchdog_timer = self.create_timer(1.0, self.watchdog_callback)

        # 바운딩 박스 색상 팔레트 (RGB/BGR 고대비 네온 색상)
        self.color_palette = [
            (0, 255, 128),   # 에메랄드 그린
            (0, 215, 255),   # 골드/옐로우
            (255, 144, 30),  # 일렉트릭 블루
            (0, 165, 255),   # 오렌지
            (255, 0, 255),   # 마젠타
            (0, 255, 255),   # 옐로우
        ]

        # 구독자 등록
        self.sub_cam = self.create_subscription(
            CompressedImage,
            '/camera/image_raw/compressed',
            self.image_callback,
            qos_realtime
        )
        self.sub_json = self.create_subscription(
            String,
            '/yolo/detections',
            self.json_callback,
            10
        )
        self.sub_gesture_state = self.create_subscription(
            String,
            '/gesture/state',
            self.gesture_state_callback,
            10
        )
        self.sub_gesture_cmd = self.create_subscription(
            String,
            '/gesture/cmd',
            self.gesture_cmd_callback,
            10
        )
        self.sub_floor_obstacles = self.create_subscription(
            String,
            '/vision/floor_obstacles',
            self.floor_obstacles_callback,
            5
        )
        self.pub_tts_say = self.create_publisher(String, '/tts/say', 10)
        self._gesture_nav_lock = threading.Lock()
        self._last_gesture_nav_ts = 0.0
        self._gesture_auto_patrol_cancel = False
        self.sub_opencr = self.create_subscription(
            BatteryState,
            '/battery_state',
            self.opencr_callback,
            qos_realtime
        )
        self.sub_sensor_state = self.create_subscription(
            SensorState,
            '/sensor_state',
            self.sensor_state_callback,
            qos_realtime
        )
        self.sub_pi5 = self.create_subscription(
            BatteryState,
            '/pi5/battery_state',
            self.pi5_callback,
            qos_realtime
        )
        self.sub_scan = self.create_subscription(
            LaserScan,
            '/scan',
            self.scan_callback,
            qos_realtime
        )

        # 모터 토크 제어 서비스 클라이언트 (/motor_power)
        self.cli_motor_power = self.create_client(SetBool, '/motor_power')
        # Nav2 단일 목적지 액션 클라이언트 (/navigate_to_pose) 및 전체 취소 서비스
        self.nav_action_client = ActionClient(self, NavigateToPose, '/navigate_to_pose')
        self.cli_nav_cancel = self.create_client(CancelGoal, '/navigate_to_pose/_action/cancel_goal')
        # 순찰 루프 제어 서비스 및 상태 구독 (/patrol/set_enabled, /patrol/status)
        self.cli_patrol = self.create_client(SetBool, '/patrol/set_enabled')
        # Costmap Filter 마스크 실시간 리로드 서비스 (/keepout_filter_mask_server/load_map, /speed_filter_mask_server/load_map)
        self.cli_keepout_load = self.create_client(LoadMap, '/keepout_filter_mask_server/load_map')
        self.cli_speed_load = self.create_client(LoadMap, '/speed_filter_mask_server/load_map')
        self.sub_patrol_status = self.create_subscription(
            String,
            '/patrol/status',
            self.patrol_status_callback,
            10
        )
        GlobalState.ros_node_ref = self

        # VLM 수동 브리핑 트리거 발행자 (/vlm/trigger) 및 응답 구독 (/vlm/response)
        self.pub_vlm_trigger = self.create_publisher(String, '/vlm/trigger', 10)
        # 사람탐지 구역 연동 제어 (/yolo/zone_enable)
        self.pub_yolo_zone_enable = self.create_publisher(Bool, '/yolo/zone_enable', 10)
        self._last_yolo_zone_enable = None
        self.yolo_zone_timer = self.create_timer(1.0, self.evaluate_and_publish_yolo_zone)
        self.sub_vlm_response = self.create_subscription(
            String,
            '/vlm/response',
            self.vlm_response_callback,
            10
        )
        self.pub_cmd_vel = self.create_publisher(Twist, '/cmd_vel', 10)

        # 효과음/음악 재생 발행자 (/play/sound)
        self.pub_play_sound = self.create_publisher(String, '/play/sound', 10)

        # 현장 USB 스피커 볼륨 제어 토픽 (/speaker/volume_set, /speaker/volume_state)
        self.pub_speaker_volume = self.create_publisher(Int32, '/speaker/volume_set', 10)
        self.sub_speaker_volume = self.create_subscription(
            Int32,
            '/speaker/volume_state',
            self.speaker_volume_callback,
            10
        )

        # 2D 실시간 맵(/map, /amcl_pose, /plan) 구독
        qos_map = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.sub_map = self.create_subscription(OccupancyGrid, '/map', self.map_callback, qos_map)
        self.sub_amcl = self.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', self.amcl_pose_callback, qos_map)
        self.sub_plan = self.create_subscription(Path, '/plan', self.plan_callback, 10)
        self.current_odom_yaw = None
        self.turn_cancel_event = threading.Event()
        self.sub_odom = self.create_subscription(Odometry, '/odom', self.odom_callback, qos_realtime)

        self.get_logger().info("==================================================")
        self.get_logger().info("  🌐 JTA Bot WebUI & Health Server Started ")
        self.get_logger().info(f"  📡 Web Port: 9999 (http://{LAPTOP_IP}:9999)")
        self.get_logger().info("==================================================")

    def get_nav_mode(self) -> str:
        """ROS 2 그래프에서 현재 활성화된 Nav2 / SLAM 모드를 실시간 판별"""
        try:
            nodes = set(self.get_node_names())
        except Exception:
            return "IDLE"
        if "jta_auto_localizer" in nodes:
            return "LOCALIZING"
        if nodes & {"amcl", "bt_navigator", "controller_server", "planner_server"}:
            return "NAV2"
        if nodes & {"slam_toolbox", "cartographer_node"}:
            return "SLAM"
        return "IDLE"

    def trigger_vlm_briefing(self):
        """WebUI로부터 요청받은 VLM 현장 상황 즉시 브리핑을 젯슨으로 전달"""
        msg = String()
        msg.data = "즉시"
        self.pub_vlm_trigger.publish(msg)
        return True, "VLM 현장 상황 브리핑을 요청했습니다."

    def vlm_response_callback(self, msg: String):
        """Jetson VLM 응답(/vlm/response) 수신 시 대시보드 채팅창용 상태 갱신"""
        text = str(msg.data or '').strip()
        if not text:
            return
        with GlobalState.telemetry_lock:
            cur = GlobalState.telemetry.get('vlm_response', {})
            seq = int(cur.get('seq', 0)) + 1
            GlobalState.telemetry['vlm_response'] = {
                'text': text,
                'ts': time.time(),
                'seq': seq,
            }

    def ask_vlm(self, prompt: str) -> tuple[bool, str, int]:
        """대시보드 채팅창 질문을 /vlm/trigger로 발행 (답변은 Jetson -> /vlm/response -> Pi 5 TTS 자동 출력)"""
        clean_prompt = prompt.strip()[:200]
        if not clean_prompt:
            return False, "질문 내용이 비어 있습니다.", 0
        with GlobalState.telemetry_lock:
            seq_before = int(GlobalState.telemetry.get('vlm_response', {}).get('seq', 0))
        msg = String()
        msg.data = clean_prompt
        self.pub_vlm_trigger.publish(msg)
        return True, "VLM 질문 전송 완료 (TTS 출력 대기)", seq_before

    def _stop_cmd_vel_and_torque(self):
        """종료 시 /cmd_vel 0.0을 5회 발행하고 모터 토크를 안전 차단"""
        stop_msg = Twist()
        for _ in range(5):
            self.pub_cmd_vel.publish(stop_msg)
            time.sleep(0.05)
        self.set_motor_torque(False)

    def manage_nav_mode(self, action: str):
        """Pi 5 원격 Nav2 / SLAM 시작·종료, 초기 위치 재탐색 및 주행 전체 초기화 제어"""
        valid_actions = {"nav2_start", "nav2_stop", "slam_start", "slam_stop", "relocalize", "full_reset"}
        if action not in valid_actions:
            return False, f"지원하지 않는 명령입니다: {action}"

        pre_clean = (
            "pkill -9 -f '[s]lam_toolbox|[c]artographer|[n]av2_|[n]av2\\.launch\\.py|[b]ringup_launch|"
            "[a]mcl|[m]ap_server|[p]lanner_server|[c]ontroller_server|"
            "[b]t_navigator|[a]uto_localizer|[p]atrol_node|[b]ehavior_server|[s]moother_server|"
            "[v]elocity_smoother|[w]aypoint_follower' || true"
        )

        def run_pi5_cmd(cmd_str: str, timeout_sec: int = 12):
            if IS_PI5_LOCAL:
                return subprocess.run(["bash", "-c", cmd_str], capture_output=True, text=True, timeout=timeout_sec)
            return subprocess.run(ssh_base + [cmd_str], capture_output=True, text=True, timeout=timeout_sec)

        ssh_base = [
            "ssh", "-F", "/dev/null", "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{PI5_USER}@{PI5_IP}"
        ]
        try:
            if action == "relocalize":
                if self.get_nav_mode() != "NAV2":
                    return False, "Nav2가 실행 중일 때만 초기 위치 재탐색이 가능합니다."
                res_clean_loc = run_pi5_cmd("pkill -9 -f '[a]uto_localizer' || true", timeout_sec=10)
                if res_clean_loc.returncode != 0:
                    err = (res_clean_loc.stderr or res_clean_loc.stdout or f"정리 실패 (rc={res_clean_loc.returncode})").strip()
                    return False, f"Pi 5 제어 실패: {err}"
                inner_reloc = (
                    "source /opt/ros/humble/setup.bash && "
                    f"source {PI5_HOME}/ros2_ws/install/setup.bash && "
                    "export ROS_DOMAIN_ID=30 RMW_IMPLEMENTATION=rmw_cyclonedds_cpp "
                    f"CYCLONEDDS_URI=file://{PI5_HOME}/cyclonedds.xml && "
                    "ros2 run jta_bringup auto_localizer --ros-args -p enable_spin:=true"
                )
                if IS_PI5_LOCAL:
                    reloc_cmd = f"setsid -f bash -c '{inner_reloc}' > {PI5_HOME}/relocalize.log 2>&1 < /dev/null"
                else:
                    reloc_cmd = f"setsid -f distrobox enter humble -- bash -c '{inner_reloc}' > {PI5_HOME}/relocalize.log 2>&1 < /dev/null"
                self.set_motor_torque(True)
                res_reloc = run_pi5_cmd(reloc_cmd, timeout_sec=12)
                if res_reloc.returncode != 0:
                    err = (res_reloc.stderr or res_reloc.stdout or f"실행 실패 (rc={res_reloc.returncode})").strip()
                    return False, f"Pi 5 제어 실패: {err}"
                return True, "AMCL 초기 위치 재탐색(제자리 회전 수렴)을 시작했습니다."

            # 정지/초기화 명령 시 네트워크 왕복 전에 즉시 모터 브레이크(/cmd_vel 0.0 x5 + 토크 차단) 선행
            if action in {"nav2_stop", "slam_stop", "full_reset"}:
                self._stop_cmd_vel_and_torque()

            # 1단계: 기존 Nav2/SLAM 프로세스 안전 정리 (커맨드라인 자폭 방지를 위해 start와 분리 실행)
            res_clean = run_pi5_cmd(pre_clean, timeout_sec=10)
            if res_clean.returncode != 0:
                err = (res_clean.stderr or res_clean.stdout or f"Pre-Clean 실패 (rc={res_clean.returncode})").strip()
                return False, f"Pi 5 제어 실패: {err}"

            if action == "full_reset":
                if not IS_PI5_LOCAL:
                    # foxglove_bridge 재기동으로 유령 /cmd_vel 퍼블리셔 및 /tf_static 상태 초기화 (노트북 전용)
                    subprocess.run(["pkill", "-9", "-f", "[f]oxglove_bridge"], capture_output=True, timeout=5)
                    time.sleep(0.5)
                    subprocess.Popen(
                        [
                            "bash", "-c",
                            "source /opt/ros/humble/setup.bash && "
                            "export ROS_DOMAIN_ID=30 RMW_IMPLEMENTATION=rmw_cyclonedds_cpp "
                            "CYCLONEDDS_URI=file:///root/ros2_JTA/config/cyclonedds.xml && "
                            "ros2 launch foxglove_bridge foxglove_bridge_launch.xml port:=8765 "
                            "> /tmp/foxglove_bridge.log 2>&1"
                        ],
                        start_new_session=True,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                return True, "주행 계통 전체 초기화를 완료했습니다. (OpenCR 정지 · Nav2/SLAM 정리)"

            if action == "nav2_start":
                start_cmd = (
                    f"setsid -f {PI5_HOME}/inner_nav2_launch.sh > {PI5_HOME}/nav2.log 2>&1 < /dev/null"
                    if IS_PI5_LOCAL else
                    f"setsid -f distrobox enter humble -- {PI5_HOME}/inner_nav2_launch.sh "
                    f"> {PI5_HOME}/nav2.log 2>&1 < /dev/null"
                )
                msg = "Nav2 자율주행을 기동했습니다. (기존 SLAM 자동 정리)"
            elif action == "slam_start":
                start_cmd = (
                    f"setsid -f {PI5_HOME}/inner_slam_launch.sh > {PI5_HOME}/slam.log 2>&1 < /dev/null"
                    if IS_PI5_LOCAL else
                    f"setsid -f distrobox enter humble -- {PI5_HOME}/inner_slam_launch.sh "
                    f"> {PI5_HOME}/slam.log 2>&1 < /dev/null"
                )
                msg = "SLAM 지도 작성을 기동했습니다. (기존 Nav2 자동 정리)"
            else:
                self._stop_cmd_vel_and_torque()
                msg = "Nav2를 종료하고 모터를 정지했습니다." if action == "nav2_stop" else "SLAM을 종료하고 모터를 정지했습니다."
                return True, msg

            # 2단계: 선택된 모드 백그라운드 기동
            time.sleep(0.5)
            res = run_pi5_cmd(start_cmd, timeout_sec=12)
            if res.returncode != 0:
                err = (res.stderr or res.stdout or f"실행 실패 (rc={res.returncode})").strip()
                return False, f"Pi 5 제어 실패: {err}"
            if action == "slam_start":
                self.set_motor_torque(True)
            return True, msg
        except Exception as e:
            return False, f"Pi 5 원격 제어 예외: {e}"

    def play_sound(self, sound_name="dobingda"):
        """WebUI로부터 요청받은 효과음/음악 재생 명령을 Pi 5 스피커로 전달"""
        msg = String()
        msg.data = sound_name
        self.pub_play_sound.publish(msg)
        return True, f"'{sound_name}' 사운드 재생 요청을 전달했습니다."

    def speaker_volume_callback(self, msg: Int32):
        """Pi 5 스피커 노드로부터 수신한 실제 하드웨어 볼륨 상태 동기화"""
        vol = int(msg.data)
        with GlobalState.telemetry_lock:
            GlobalState.current_volume = vol
            if 'tier1_speaker' not in GlobalState.telemetry:
                GlobalState.telemetry['tier1_speaker'] = {}
            GlobalState.telemetry['tier1_speaker']['volume'] = vol

    def set_speaker_volume(self, volume: int):
        """WebUI로부터 요청받은 볼륨(0~100)을 Pi 5 스피커로 전달"""
        vol = max(0, min(100, int(volume)))
        msg = Int32()
        msg.data = vol
        self.pub_speaker_volume.publish(msg)
        with GlobalState.telemetry_lock:
            GlobalState.current_volume = vol
            if 'tier1_speaker' not in GlobalState.telemetry:
                GlobalState.telemetry['tier1_speaker'] = {}
            GlobalState.telemetry['tier1_speaker']['volume'] = vol
        return True, f"볼륨이 {vol}%로 설정되었습니다."

    def sensor_state_callback(self, msg):
        now = time.time()
        self.last_opencr_time = now
        with GlobalState.telemetry_lock:
            GlobalState.telemetry['tier1_opencr']['torque'] = bool(msg.torque)

    def set_motor_torque(self, enable: bool):
        """WebUI로부터 요청받은 모터 토크 ON/OFF를 OpenCR /motor_power 서비스로 전달"""
        if not self.cli_motor_power.service_is_ready():
            if not self.cli_motor_power.wait_for_service(timeout_sec=1.5):
                return False, "/motor_power 서비스를 찾을 수 없습니다 (OpenCR 미구동)"

        req = SetBool.Request()
        req.data = enable
        future = self.cli_motor_power.call_async(req)

        # 2초 내 완료 대기
        start_t = time.time()
        while not future.done() and (time.time() - start_t < 2.0):
            time.sleep(0.05)

        if future.done():
            res = future.result()
            if res.success:
                with GlobalState.telemetry_lock:
                    GlobalState.telemetry['tier1_opencr']['torque'] = enable
                return True, res.message
            return False, res.message
        return False, "서비스 응답 시간 초과"

    def patrol_status_callback(self, msg):
        new_status = str(msg.data or 'IDLE')
        with GlobalState.telemetry_lock:
            prev_status = str(GlobalState.telemetry.get('patrol_status', 'IDLE'))
            GlobalState.telemetry['patrol_status'] = new_status
        if prev_status.startswith('FOLLOWING') != new_status.startswith('FOLLOWING'):
            self.evaluate_and_publish_yolo_zone()

    def set_patrol_enabled(self, enable: bool):
        """WebUI로부터 요청받은 순찰 루프 ON/OFF를 Pi 5 /patrol/set_enabled 서비스로 전달"""
        if not self.cli_patrol.service_is_ready():
            if not self.cli_patrol.wait_for_service(timeout_sec=1.5):
                return False, "/patrol/set_enabled 서비스를 찾을 수 없습니다 (Nav2/patrol_node 미구동)"

        req = SetBool.Request()
        req.data = enable
        future = self.cli_patrol.call_async(req)

        start_t = time.time()
        while not future.done() and (time.time() - start_t < 2.5):
            time.sleep(0.05)

        if future.done():
            res = future.result()
            if res.success:
                with GlobalState.telemetry_lock:
                    GlobalState.telemetry['patrol_status'] = 'STARTING' if enable else 'STOPPED'
                    if enable:
                        prev_seq = int(GlobalState.telemetry.get('wp_goal_status', {}).get('seq', 0))
                        GlobalState.telemetry['wp_goal_status'] = {
                            'state': 'IDLE',
                            'target': '',
                            'ts': time.time(),
                            'seq': prev_seq + 1,
                        }
                return True, res.message
            return False, res.message
        return False, "순찰 서비스 응답 시간 초과"

    def send_waypoint_goal(self, wp_index: int) -> tuple[bool, str]:
        """등록된 웨이포인트 중 지정 인덱스로 단일 NavigateToPose Goal 전송."""
        wps = self.load_patrol_waypoints()
        if wp_index < 0 or wp_index >= len(wps):
            return False, f"유효하지 않은 웨이포인트 번호입니다: {wp_index}"

        # 루프 순찰이 동작 중이면 Goal 충돌 방지를 위해 먼저 순찰 중지
        with GlobalState.telemetry_lock:
            patrol_status = str(GlobalState.telemetry.get('patrol_status', 'IDLE'))
        if patrol_status.startswith(('NAVIGATING', 'DWELLING', 'RETRY', 'STARTING')):
            self.set_patrol_enabled(False)
            time.sleep(0.2)

        if not self.nav_action_client.wait_for_server(timeout_sec=1.5):
            return False, "Nav2 /navigate_to_pose 액션 서버를 찾을 수 없습니다 (Nav2 미구동)"

        wp = wps[wp_index]
        yaw = float(wp.get('yaw', 0.0))
        goal_msg = NavigateToPose.Goal()
        goal_msg.pose = PoseStamped()
        goal_msg.pose.header.frame_id = 'map'
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = float(wp['x'])
        goal_msg.pose.pose.position.y = float(wp['y'])
        goal_msg.pose.pose.position.z = 0.0
        goal_msg.pose.pose.orientation.z = math.sin(yaw * 0.5)
        goal_msg.pose.pose.orientation.w = math.cos(yaw * 0.5)

        future = self.nav_action_client.send_goal_async(goal_msg)
        start_t = time.time()
        while not future.done() and (time.time() - start_t < 2.0):
            time.sleep(0.05)

        if not future.done():
            return False, "Nav2 Goal 전송 응답 시간 초과"

        goal_handle = future.result()
        if not goal_handle or not goal_handle.accepted:
            return False, f"[{wp.get('name', f'WP{wp_index + 1}')}] Nav2가 목표를 거부했습니다."

        name = wp.get('name', f'WP{wp_index + 1}')
        with GlobalState.telemetry_lock:
            prev_seq = int(GlobalState.telemetry.get("wp_goal_status", {}).get("seq", 0))
            goal_seq = prev_seq + 1
            GlobalState.telemetry["wp_goal_status"] = {
                "state": "MOVING",
                "target": name,
                "ts": time.time(),
                "seq": goal_seq,
            }

        def _on_goal_result(res_fut, seq=goal_seq, target_name=name):
            try:
                res = res_fut.result()
                status_code = getattr(res, "status", None)
                if status_code == GoalStatus.STATUS_SUCCEEDED:
                    next_state = "SUCCEEDED"
                elif status_code == GoalStatus.STATUS_CANCELED:
                    next_state = "CANCELED"
                else:
                    next_state = "FAILED"
            except Exception:
                next_state = "FAILED"
            with GlobalState.telemetry_lock:
                cur = GlobalState.telemetry.get("wp_goal_status", {})
                if int(cur.get("seq", 0)) == seq:
                    GlobalState.telemetry["wp_goal_status"] = {
                        "state": next_state,
                        "target": target_name,
                        "ts": time.time(),
                        "seq": seq,
                    }

        goal_handle.get_result_async().add_done_callback(_on_goal_result)
        return True, f"[{name}] 단일 이동 시작 (x={float(wp['x']):.2f}m, y={float(wp['y']):.2f}m)"

    def cancel_all_navigation(self) -> tuple[bool, str]:
        """모터 토크와 맵/AMCL은 유지한 채 순찰·Nav2 목표를 모두 취소하고 즉시 정지."""
        self.turn_cancel_event.set()

        # 1. 순찰 중이면 먼저 중지 & 단일 WP 이동 상태 취소 반영
        with GlobalState.telemetry_lock:
            patrol_status = str(GlobalState.telemetry.get('patrol_status', 'IDLE'))
            wp_goal = GlobalState.telemetry.get('wp_goal_status', {})
            if wp_goal.get('state') == 'MOVING':
                GlobalState.telemetry['wp_goal_status'] = {
                    'state': 'CANCELED',
                    'target': wp_goal.get('target', ''),
                    'ts': time.time(),
                    'seq': int(wp_goal.get('seq', 0)),
                }
        if patrol_status.startswith(('NAVIGATING', 'DWELLING', 'RETRY', 'STARTING')):
            self.set_patrol_enabled(False)

        # 2. Nav2 /navigate_to_pose 모든 활성 Goal 취소 (발신자 무관)
        if self.cli_nav_cancel.wait_for_service(timeout_sec=0.8):
            future = self.cli_nav_cancel.call_async(CancelGoal.Request())
            start_t = time.time()
            while not future.done() and (time.time() - start_t < 1.2):
                time.sleep(0.05)

        # 3. 모터 토크는 유지한 채 /cmd_vel 0.0 5회 발행으로 즉시 브레이크
        stop_msg = Twist()
        for _ in range(5):
            if rclpy.ok():
                self.pub_cmd_vel.publish(stop_msg)
            time.sleep(0.04)

        return True, "주행 취소 완료 (모터 토크 유지 · 다음 명령 대기)"

    def odom_callback(self, msg: Odometry):
        q = msg.pose.pose.orientation
        self.current_odom_yaw = math.atan2(
            2.0 * (q.w * q.z + q.x * q.y),
            1.0 - 2.0 * (q.y * q.y + q.z * q.z),
        )

    def publish_cmd_vel(self, linear: float, angular: float) -> bool:
        """2P Teleop 패드로부터 수신한 속도 명령을 안전 범위로 제한해 /cmd_vel 발행"""
        self.turn_cancel_event.set()
        msg = Twist()
        msg.linear.x = max(-0.25, min(0.25, float(linear)))
        msg.angular.z = max(-1.0, min(1.0, float(angular)))
        self.pub_cmd_vel.publish(msg)
        return True

    def start_turn_angle(self, deg: float) -> tuple[bool, str]:
        """/odom 피드백 기반으로 지정 각도(90/-90/180도) 제자리 회전"""
        self.turn_cancel_event.set()
        time.sleep(0.06)
        self.turn_cancel_event.clear()

        target_rad = math.radians(max(-180.0, min(180.0, float(deg))))

        def _worker():
            sign = 1.0 if target_rad >= 0.0 else -1.0
            abs_target = abs(target_rad)
            max_dur = abs_target / 0.35 + 3.0
            start_t = time.time()

            if self.current_odom_yaw is not None:
                prev_yaw = self.current_odom_yaw
                accum = 0.0
                while not self.turn_cancel_event.is_set() and (time.time() - start_t < max_dur):
                    cur = self.current_odom_yaw
                    dyaw = math.atan2(math.sin(cur - prev_yaw), math.cos(cur - prev_yaw))
                    accum += dyaw
                    prev_yaw = cur
                    rem = abs_target - abs(accum)
                    if rem <= 0.03:
                        break
                    speed = 0.70 if rem > 0.35 else max(0.25, rem * 1.8)
                    msg = Twist()
                    msg.angular.z = sign * speed
                    self.pub_cmd_vel.publish(msg)
                    time.sleep(0.05)
            else:
                dur = abs_target / 0.70
                while not self.turn_cancel_event.is_set() and (time.time() - start_t < dur):
                    msg = Twist()
                    msg.angular.z = sign * 0.70
                    self.pub_cmd_vel.publish(msg)
                    time.sleep(0.05)

            stop_msg = Twist()
            for _ in range(3):
                self.pub_cmd_vel.publish(stop_msg)
                time.sleep(0.04)

        threading.Thread(target=_worker, daemon=True).start()
        return True, f"{deg:+.0f}° 회전 시작"

    def _get_waypoints_yaml_path(self) -> str:
        candidates = [
            os.path.expanduser('~/ros2_ws/src/jta_bringup/config/patrol_waypoints.yaml'),
            '/root/ros2_JTA/src/pi5/jta_bringup/config/patrol_waypoints.yaml',
            os.path.join(REPO_ROOT, 'src', 'pi5', 'jta_bringup', 'config', 'patrol_waypoints.yaml'),
        ]
        for p in candidates:
            if os.path.exists(os.path.dirname(p)):
                return p
        return candidates[0]

    def load_patrol_waypoints(self) -> list:
        path = self._get_waypoints_yaml_path()
        if not os.path.isfile(path):
            return []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            return list(data.get('patrol', {}).get('waypoints', []) or [])
        except Exception:
            return []

    def load_dock_pose(self) -> dict | None:
        path = self._get_waypoints_yaml_path()
        if not os.path.isfile(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            d = data.get('patrol', {}).get('dock')
            if isinstance(d, dict) and 'x' in d and 'y' in d:
                return {'x': float(d['x']), 'y': float(d['y']), 'yaw': float(d.get('yaw', 0.0))}
            return None
        except Exception:
            return None

    def _save_and_sync_waypoints(self, waypoints: list, custom_order: list[int] | None = None, dock: dict | None = None, keep_dock: bool = True) -> tuple[bool, str]:
        path = self._get_waypoints_yaml_path()
        saved_dock = dock if dock is not None else (self.load_dock_pose() if keep_dock else None)
        patrol_block = {
            'dwell_sec': 2.0,
            'retry_limit': 1,
            'custom_order': list(custom_order) if custom_order else [],
            'waypoints': waypoints,
        }
        if saved_dock is not None:
            patrol_block['dock'] = saved_dock
        payload = {'patrol': patrol_block}
        try:
            with open(path, 'w', encoding='utf-8') as f:
                yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)
        except Exception as exc:
            return False, f"로컬 YAML 저장 실패: {exc}"

        if IS_PI5_LOCAL:
            install_dst = os.path.expanduser("~/ros2_ws/install/jta_bringup/share/jta_bringup/config/patrol_waypoints.yaml")
            try:
                os.makedirs(os.path.dirname(install_dst), exist_ok=True)
                shutil.copy2(path, install_dst)
            except Exception as exc:
                return True, f"로컬 저장 완료 (install 복사 실패: {exc})"
            return True, "Pi 5 로컬 저장 및 동기화 완료"

        scp_base = [
            "scp", "-F", "/dev/null", "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=4", path
        ]
        targets = [
            f"{PI5_USER}@{PI5_IP}:{PI5_HOME}/ros2_ws/src/jta_bringup/config/patrol_waypoints.yaml",
            f"{PI5_USER}@{PI5_IP}:{PI5_HOME}/ros2_ws/install/jta_bringup/share/jta_bringup/config/patrol_waypoints.yaml",
        ]
        for dst in targets:
            res = subprocess.run(scp_base + [dst], capture_output=True, text=True, timeout=8)
            if res.returncode != 0:
                return True, f"로컬 저장 완료 (Pi 5 동기화 보류: {res.stderr.strip() or '오프라인'})"
        return True, "저장 및 Pi 5 동기화 완료"

    def apply_custom_patrol_order(self, ordered_indices: list[int]) -> tuple[bool, str]:
        wps = self.load_patrol_waypoints()
        if not wps:
            return False, "등록된 웨이포인트가 없습니다."
        valid_order = []
        for idx in ordered_indices:
            if 0 <= idx < len(wps):
                valid_order.append(idx)
        if not valid_order:
            return False, "지정순찰할 웨이포인트를 1개 이상 선택해 주세요."
        for wp in wps:
            wp['enabled'] = True
        return self._save_and_sync_waypoints(wps, custom_order=valid_order)

    def clear_custom_patrol_order(self) -> tuple[bool, str]:
        wps = self.load_patrol_waypoints()
        if not wps:
            return False, "등록된 웨이포인트가 없습니다."
        for wp in wps:
            wp['enabled'] = True
        return self._save_and_sync_waypoints(wps, custom_order=[])

    def set_current_dock_pose(self) -> tuple[bool, str, dict | None]:
        with GlobalState.map_lock:
            pose = GlobalState.map_state.get("robot_pose")
        if pose is None:
            return False, "로봇 현재 위치(/amcl_pose)를 아직 수신하지 못했습니다.", self.load_dock_pose()
        dock = {
            'x': round(float(pose['x']), 3),
            'y': round(float(pose['y']), 3),
            'yaw': round(float(pose['yaw']), 3),
        }
        wps = self.load_patrol_waypoints()
        ok, sync_msg = self._save_and_sync_waypoints(wps, dock=dock)
        if not ok:
            return False, sync_msg, dock
        return True, f"충전소(DOCK) 지정 완료 ({dock['x']}, {dock['y']}) · {sync_msg}", dock

    def add_current_patrol_waypoint(self) -> tuple[bool, str, list]:
        with GlobalState.map_lock:
            pose = GlobalState.map_state.get("robot_pose")
        if pose is None:
            return False, "로봇 현재 위치(/amcl_pose)를 아직 수신하지 못했습니다.", self.load_patrol_waypoints()

        wps = self.load_patrol_waypoints()
        if len(wps) >= 20:
            return False, "순찰 코스는 최대 20개까지만 저장할 수 있습니다.", wps

        idx = len(wps) + 1
        wps.append({
            'name': f'WP{idx}',
            'x': round(float(pose['x']), 3),
            'y': round(float(pose['y']), 3),
            'yaw': round(float(pose['yaw']), 3),
        })
        ok, sync_msg = self._save_and_sync_waypoints(wps)
        if not ok:
            return False, sync_msg, wps
        return True, f"WP{idx} 추가 ({len(wps)}/20) · {sync_msg}", wps

    def delete_patrol_waypoint(self, wp_index: int) -> tuple[bool, str, list]:
        wps = self.load_patrol_waypoints()
        if wp_index < 0 or wp_index >= len(wps):
            return False, f"유효하지 않은 웨이포인트 번호입니다: {wp_index}", wps

        removed = wps.pop(wp_index)
        removed_name = str(removed.get('name', f'WP{wp_index + 1}'))
        for i, wp in enumerate(wps):
            wp['name'] = f'WP{i + 1}'

        ok, sync_msg = self._save_and_sync_waypoints(wps)
        if not ok:
            return False, sync_msg, self.load_patrol_waypoints()
        return True, f"{removed_name} 삭제 완료 ({len(wps)}/20) · {sync_msg}", wps

    def clear_patrol_waypoints(self) -> tuple[bool, str, list]:
        ok, sync_msg = self._save_and_sync_waypoints([])
        if not ok:
            return False, sync_msg, self.load_patrol_waypoints()
        return True, f"순찰 코스 초기화 완료 (0/20) · {sync_msg}", []

    def _get_detection_zones_json_path(self) -> str:
        return os.path.join(os.path.dirname(self._get_waypoints_yaml_path()), 'detection_zones.json')

    def load_detection_zones(self) -> list:
        path = self._get_detection_zones_json_path()
        if not os.path.isfile(path):
            return []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return list(data) if isinstance(data, list) else []
        except Exception:
            return []

    def save_detection_zones(self, zones: list) -> tuple[bool, str, list]:
        if len(zones) > 20:
            return False, "사람탐지 구역은 최대 20개까지만 저장할 수 있습니다.", self.load_detection_zones()
        path = self._get_detection_zones_json_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(zones, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            return False, f"탐지 구역 저장 실패: {exc}", self.load_detection_zones()
        self.evaluate_and_publish_yolo_zone()
        return True, f"사람탐지 구역 저장 완료 ({len(zones)}개)", zones

    def get_yolo_zone_state(self) -> dict:
        zones = self.load_detection_zones()
        with GlobalState.telemetry_lock:
            patrol_status = str(GlobalState.telemetry.get('patrol_status', 'IDLE'))
        if patrol_status.startswith('FOLLOWING'):
            return {"has_zones": bool(zones), "active": True, "following_lock": True}
        if not zones:
            return {"has_zones": False, "active": True}
        with GlobalState.map_lock:
            pose = GlobalState.map_state.get("robot_pose")
        if not pose:
            return {"has_zones": True, "active": False}
        rx = float(pose["x"])
        ry = float(pose["y"])
        for z in zones:
            if not isinstance(z, dict):
                continue
            x1, x2 = float(z.get("x1", 0.0)), float(z.get("x2", 0.0))
            y1, y2 = float(z.get("y1", 0.0)), float(z.get("y2", 0.0))
            if min(x1, x2) <= rx <= max(x1, x2) and min(y1, y2) <= ry <= max(y1, y2):
                return {"has_zones": True, "active": True}
        return {"has_zones": True, "active": False}

    def evaluate_and_publish_yolo_zone(self):
        st = self.get_yolo_zone_state()
        active = bool(st["active"])
        if self._last_yolo_zone_enable != active:
            self._last_yolo_zone_enable = active
            self.get_logger().info(
                f">>> [YOLO Zone] /yolo/zone_enable={active} (구역설정={'ON' if st['has_zones'] else '없음'})"
            )
        msg = Bool()
        msg.data = active
        if rclpy.ok():
            self.pub_yolo_zone_enable.publish(msg)

    def _get_hazard_zones_json_path(self) -> str:
        return os.path.join(os.path.dirname(self._get_waypoints_yaml_path()), 'hazard_zones.json')

    def load_hazard_zones(self) -> list:
        path = self._get_hazard_zones_json_path()
        if not os.path.isfile(path):
            return []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return list(data) if isinstance(data, list) else []
        except Exception:
            return []

    def _rasterize_and_write_masks(self, zones: list, out_dir: str) -> tuple[bool, str]:
        with GlobalState.map_lock:
            ms = GlobalState.map_state
            w = int(ms.get("width", 0))
            h = int(ms.get("height", 0))
            res = float(ms.get("resolution", 0.05) or 0.05)
            ox = float(ms.get("origin_x", 0.0))
            oy = float(ms.get("origin_y", 0.0))

        if w <= 0 or h <= 0 or res <= 0:
            return False, "현재 /map 메타데이터를 수신하지 못했습니다 (Nav2/SLAM 기동 후 시도해 주세요)."

        os.makedirs(out_dir, exist_ok=True)
        keepout_img = np.full((h, w), 254, dtype=np.uint8)
        speed_img = np.full((h, w), 255, dtype=np.uint8)

        def to_px(wx: float, wy: float) -> tuple[int, int]:
            px = int(round((wx - ox) / res))
            py = h - 1 - int(round((wy - oy) / res))
            return px, py

        for z in zones:
            if not isinstance(z, dict):
                continue
            ztype = str(z.get("type", "keepout"))
            shape = str(z.get("shape", "line"))
            x1 = float(z.get("x1", 0.0))
            y1 = float(z.get("y1", 0.0))
            x2 = float(z.get("x2", 0.0))
            y2 = float(z.get("y2", 0.0))
            p1 = to_px(x1, y1)
            p2 = to_px(x2, y2)

            if ztype == "keepout":
                if shape == "rect":
                    cv2.rectangle(keepout_img, p1, p2, 0, thickness=-1)
                else:
                    thick_px = max(4, int(round(max(0.25, float(z.get("thickness", 0.25))) / res)))
                    cv2.line(keepout_img, p1, p2, 0, thickness=thick_px)
            elif ztype == "speed":
                pct = max(10, min(95, int(z.get("speed_pct", 40))))
                # SpeedFilter(type=1, base=100, multiplier=-1): occ = 100 - pct, pixel = 255 * (100 - occ) / 100
                pixel_val = max(1, min(254, int(round(255.0 * pct / 100.0))))
                if shape == "rect":
                    cv2.rectangle(speed_img, p1, p2, pixel_val, thickness=-1)
                else:
                    thick_px = max(3, int(round(float(z.get("thickness", 0.25)) / res)))
                    cv2.line(speed_img, p1, p2, pixel_val, thickness=thick_px)

        def write_pgm_and_yaml(name: str, img_arr: np.ndarray, yaml_meta: dict):
            pgm_path = os.path.join(out_dir, f"{name}.pgm")
            yaml_path = os.path.join(out_dir, f"{name}.yaml")
            with open(pgm_path, 'wb') as f:
                f.write(f"P5\n{w} {h}\n255\n".encode('ascii'))
                f.write(img_arr.tobytes())
            with open(yaml_path, 'w', encoding='utf-8') as f:
                yaml.safe_dump(yaml_meta, f, sort_keys=False)

        write_pgm_and_yaml("keepout_mask", keepout_img, {
            "image": "keepout_mask.pgm",
            "mode": "trinary",
            "resolution": res,
            "origin": [ox, oy, 0.0],
            "negate": 0,
            "occupied_thresh": 0.65,
            "free_thresh": 0.196,
        })
        write_pgm_and_yaml("speed_mask", speed_img, {
            "image": "speed_mask.pgm",
            "mode": "scale",
            "resolution": res,
            "origin": [ox, oy, 0.0],
            "negate": 0,
            "occupied_thresh": 1.0,
            "free_thresh": 0.0,
        })
        return True, "마스크 생성 완료"

    def _reload_filter_mask(self, client, map_url: str) -> bool:
        if not client.service_is_ready():
            if not client.wait_for_service(timeout_sec=1.0):
                return False
        req = LoadMap.Request()
        req.map_url = map_url
        future = client.call_async(req)
        deadline = time.time() + 3.0
        while not future.done() and time.time() < deadline:
            time.sleep(0.05)
        if not future.done() or future.result() is None:
            return False
        return int(future.result().result) == 0

    def save_and_sync_hazard_zones(self, zones: list) -> tuple[bool, str, list]:
        if len(zones) > 50:
            return False, "위험구간은 최대 50개까지만 저장할 수 있습니다.", self.load_hazard_zones()

        json_path = self._get_hazard_zones_json_path()
        try:
            os.makedirs(os.path.dirname(json_path), exist_ok=True)
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(zones, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            return False, f"로컬 JSON 저장 실패: {exc}", self.load_hazard_zones()

        mask_dir = "/tmp/jta_hazard_masks"
        ok_mask, mask_msg = self._rasterize_and_write_masks(zones, mask_dir)
        if not ok_mask:
            return False, mask_msg, zones

        files = [
            os.path.join(mask_dir, "keepout_mask.pgm"),
            os.path.join(mask_dir, "keepout_mask.yaml"),
            os.path.join(mask_dir, "speed_mask.pgm"),
            os.path.join(mask_dir, "speed_mask.yaml"),
        ]
        if IS_PI5_LOCAL:
            try:
                local_maps_dir = os.path.expanduser("~/maps")
                os.makedirs(local_maps_dir, exist_ok=True)
                for fpath in files:
                    shutil.copy2(fpath, os.path.join(local_maps_dir, os.path.basename(fpath)))
            except Exception as exc:
                return True, f"로컬 저장 완료 (마스크 복사 실패: {exc})", zones
        else:
            scp_cmd = [
                "scp", "-F", "/dev/null", "-o", "StrictHostKeyChecking=no",
                "-o", "BatchMode=yes", "-o", "ConnectTimeout=4",
                *files, f"{PI5_USER}@{PI5_IP}:{PI5_HOME}/maps/"
            ]
            res = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=10)
            if res.returncode != 0:
                return True, f"로컬 저장 완료 (Pi 5 전송 보류: {res.stderr.strip() or '오프라인'})", zones

        remote_maps_dir = os.path.expanduser("~/maps") if IS_PI5_LOCAL else f"{PI5_HOME}/maps"
        k_ok = self._reload_filter_mask(self.cli_keepout_load, f"{remote_maps_dir}/keepout_mask.yaml")
        s_ok = self._reload_filter_mask(self.cli_speed_load, f"{remote_maps_dir}/speed_mask.yaml")
        if k_ok and s_ok:
            return True, f"저장·전송 및 Nav2 실시간 반영 완료 ({len(zones)}개)", zones
        return True, f"저장 및 Pi 5 전송 완료 (Nav2 재시작 시 반영 · {len(zones)}개)", zones

    def scan_callback(self, msg):
        now = time.time()
        self.last_lidar_time = now
        self.last_pi5_time = now
        self.lidar_count += 1
        if now - self.lidar_last_time >= 1.0:
            self.lidar_hz = round(self.lidar_count / (now - self.lidar_last_time), 1)
            self.lidar_count = 0
            self.lidar_last_time = now
            with GlobalState.telemetry_lock:
                GlobalState.telemetry['tier1_pi5']['lidar_status'] = f"{self.lidar_hz} Hz (정상)"

        with GlobalState.map_lock:
            pose = GlobalState.map_state.get("robot_pose")
            if pose is not None and len(msg.ranges) > 0:
                rx, ry, ryaw = pose["x"], pose["y"], pose["yaw"]
                pts = []
                step = max(1, len(msg.ranges) // 180)
                for i in range(0, len(msg.ranges), step):
                    r = msg.ranges[i]
                    if msg.range_min < r < msg.range_max and math.isfinite(r):
                        ang = ryaw + msg.angle_min + i * msg.angle_increment
                        pts.append([round(rx + r * math.cos(ang), 3), round(ry + r * math.sin(ang), 3)])
                GlobalState.map_state["scan_points"] = pts

    def map_callback(self, msg: OccupancyGrid):
        w = int(msg.info.width)
        h = int(msg.info.height)
        if w <= 0 or h <= 0:
            return
        grid = np.array(msg.data, dtype=np.int16).reshape((h, w))
        # -1(미탐색): 어두운 슬레이트, 0(빈공간): 밝은 회색, 100(벽): 진한 검정/시안
        img = np.zeros((h, w, 3), dtype=np.uint8)
        img[grid < 0] = (30, 41, 59)       # unknown (#1e293b in BGR -> RGB 비슷)
        img[grid == 0] = (226, 232, 240)   # free (#e2e8f0)
        occ_mask = grid > 0
        img[occ_mask] = (15, 23, 42)       # obstacle (#0f172a)
        # OccupancyGrid row 0은 하단(-Y)이므로 상하 반전해 이미지 좌표계로 변환
        img = np.flipud(img)
        ok, buf = cv2.imencode('.png', img)
        if not ok:
            return
        b64 = base64.b64encode(buf.tobytes()).decode('ascii')
        with GlobalState.map_lock:
            ms = GlobalState.map_state
            ms["map_version"] += 1
            ms["width"] = w
            ms["height"] = h
            ms["resolution"] = float(msg.info.resolution)
            ms["origin_x"] = float(msg.info.origin.position.x)
            ms["origin_y"] = float(msg.info.origin.position.y)
            ms["map_png_b64"] = b64

    def amcl_pose_callback(self, msg: PoseWithCovarianceStamped):
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        with GlobalState.map_lock:
            GlobalState.map_state["robot_pose"] = {
                "x": round(float(p.x), 3),
                "y": round(float(p.y), 3),
                "yaw": round(float(yaw), 4),
            }

    def plan_callback(self, msg: Path):
        poses = msg.poses
        step = max(1, len(poses) // 120) if poses else 1
        pts = [
            [round(float(ps.pose.position.x), 3), round(float(ps.pose.position.y), 3)]
            for ps in poses[::step]
        ]
        if poses and pts[-1] != [round(float(poses[-1].pose.position.x), 3), round(float(poses[-1].pose.position.y), 3)]:
            pts.append([round(float(poses[-1].pose.position.x), 3), round(float(poses[-1].pose.position.y), 3)])
        with GlobalState.map_lock:
            GlobalState.map_state["path"] = pts

    def opencr_callback(self, msg):
        now = time.time()
        self.last_opencr_time = now
        self.last_pi5_time = now
        with GlobalState.telemetry_lock:
            v = msg.voltage
            GlobalState.telemetry['tier1_opencr']['voltage'] = v
            is_ok = v > 9.0
            GlobalState.telemetry['tier1_opencr']['is_switch_on'] = is_ok
            if not is_ok:
                GlobalState.telemetry['tier1_opencr']['alert'] = "18650 스위치 OFF (0.05V)"
            elif v <= 10.8:
                GlobalState.telemetry['tier1_opencr']['alert'] = f"🚨 10.8V 이하 즉시 종료 요망 ({v:.2f}V)"
            elif v <= 11.0:
                GlobalState.telemetry['tier1_opencr']['alert'] = f"⚠️ 11.0V 이하 저전압 주의 ({v:.2f}V)"
            else:
                GlobalState.telemetry['tier1_opencr']['alert'] = f"18650 전원 정상 ({v:.2f}V)"

    def pi5_callback(self, msg):
        self.last_pi5_time = time.time()
        with GlobalState.telemetry_lock:
            GlobalState.telemetry['tier1_pi5']['voltage'] = msg.voltage
            GlobalState.telemetry['tier1_pi5']['temp'] = msg.temperature

    def json_callback(self, msg):
        now = time.time()
        self.last_jetson_time = now
        try:
            data = json.loads(msg.data)
            self.latest_detections = data
            objs = data.get('objects', [])
            img_size = data.get('image_size', [640, 480])
            if not (isinstance(img_size, list) and len(img_size) == 2):
                img_size = [640, 480]
            with GlobalState.latest_yolo_lock:
                GlobalState.latest_yolo_detections = {
                    "image_size": img_size,
                    "objects": objs,
                    "ts": now,
                }
            names = [f"{o.get('class_name', '')} ({o.get('conf', o.get('confidence', 0)):.0%})" for o in objs[:3]]
            detected_summary = ", ".join(names) if names else "없음"
            with GlobalState.telemetry_lock:
                GlobalState.telemetry['tier2_jetson']['detected_objects'] = detected_summary
        except Exception:
            pass

    def gesture_state_callback(self, msg):
        now = time.time()
        try:
            data = json.loads(msg.data)
            with GlobalState.latest_yolo_lock:
                GlobalState.latest_gesture_state = {
                    "marker_id": int(data.get("marker_id", -1)),
                    "candidate": str(data.get("candidate", "")),
                    "wp_index": int(data.get("wp_index", 0)),
                    "hold_sec": float(data.get("hold_sec", 0.0)),
                    "req_hold_sec": float(data.get("req_hold_sec", 0.8)),
                    "ts": now,
                }
        except Exception:
            pass

    def gesture_cmd_callback(self, msg):
        now = time.time()
        try:
            data = json.loads(msg.data)
            cmd = str(data.get("cmd", "")).upper()
            wp_idx = int(data.get("wp_index", 0))
            with GlobalState.latest_yolo_lock:
                GlobalState.latest_gesture_cmd = {
                    "cmd": cmd,
                    "wp_index": wp_idx,
                    "ts": now,
                }
            if cmd in ("NAV2_START", "NAV2_STOP"):
                threading.Thread(
                    target=self._handle_gesture_nav2_action,
                    args=(cmd,),
                    daemon=True,
                ).start()
        except Exception:
            pass

    def floor_obstacles_callback(self, msg):
        now = time.time()
        try:
            data = json.loads(msg.data)
            img_size = data.get("image_size", [640, 480])
            if not (isinstance(img_size, list) and len(img_size) == 2):
                img_size = [640, 480]
            with GlobalState.latest_yolo_lock:
                GlobalState.latest_floor_obstacles = {
                    "image_size": img_size,
                    "roi_top_ratio": float(data.get("roi_top_ratio", 0.60)),
                    "confirmed_beams": int(data.get("confirmed_beams", 0)),
                    "min_dist": data.get("min_dist"),
                    "boxes": data.get("boxes", []) if isinstance(data.get("boxes"), list) else [],
                    "ts": now,
                }
        except Exception:
            pass

    def _wait_and_start_auto_patrol(self):
        """Nav2 기동 및 auto_localizer 완료를 대기한 뒤 자동 순찰(/patrol/set_enabled=True) 시작."""
        deadline = time.time() + 75.0
        # 1단계: Nav2 프로세스가 기동될 때까지 잠시 대기
        time.sleep(4.0)
        while time.time() < deadline:
            if self._gesture_auto_patrol_cancel:
                self.get_logger().info("[Gesture AutoPatrol] 사용자 종료 요청으로 대기 취소")
                return
            mode = self.get_nav_mode()
            if mode == "NAV2" and self.cli_patrol.service_is_ready():
                time.sleep(1.5)
                if self._gesture_auto_patrol_cancel:
                    return
                ok, p_msg = self.set_patrol_enabled(True)
                self.get_logger().info(f"[Gesture AutoPatrol] ok={ok}, msg={p_msg}")
                if ok:
                    tts_msg = String()
                    tts_msg.data = "자동 순찰을 시작합니다."
                    self.pub_tts_say.publish(tts_msg)
                return
            time.sleep(1.5)
        self.get_logger().warn("[Gesture AutoPatrol] Nav2 준비 타임아웃(75s) -> 자동 순찰 미시작")

    def _handle_gesture_nav2_action(self, cmd: str):
        """한 손 제스처(✋ Nav2 종료 / 👍 Nav2 실행 후 자동순찰 시작) 비동기 안전 실행."""
        with self._gesture_nav_lock:
            now = time.time()
            if now - self._last_gesture_nav_ts < 5.0:
                return
            current_mode = self.get_nav_mode()
            tts_msg = String()
            if cmd == "NAV2_START":
                if current_mode == "LOCALIZING":
                    self.get_logger().info("[Gesture NAV2_START] 이미 위치 추정 중 -> 무시")
                    return
                if current_mode == "NAV2":
                    self._last_gesture_nav_ts = now
                    self._gesture_auto_patrol_cancel = False
                    ok, p_msg = self.set_patrol_enabled(True)
                    self.get_logger().info(f"[Gesture NAV2_START->Patrol] ok={ok}, msg={p_msg}")
                    if ok:
                        tts_msg.data = "자동 순찰을 시작합니다."
                        self.pub_tts_say.publish(tts_msg)
                    return
                self._last_gesture_nav_ts = now
                self._gesture_auto_patrol_cancel = False
                tts_msg.data = "내비게이션을 시작하고 자동 순찰을 준비합니다."
                self.pub_tts_say.publish(tts_msg)
                ok, res_msg = self.manage_nav_mode("nav2_start")
                self.get_logger().info(f"[Gesture NAV2_START] ok={ok}, msg={res_msg}")
                if ok:
                    threading.Thread(
                        target=self._wait_and_start_auto_patrol,
                        daemon=True,
                    ).start()
            elif cmd == "NAV2_STOP":
                self._gesture_auto_patrol_cancel = True
                if current_mode not in ("NAV2", "LOCALIZING"):
                    self.get_logger().info("[Gesture NAV2_STOP] Nav2 미실행 상태 -> 무시")
                    return
                self._last_gesture_nav_ts = now
                tts_msg.data = "내비게이션을 종료합니다."
                self.pub_tts_say.publish(tts_msg)
                ok, res_msg = self.manage_nav_mode("nav2_stop")
                self.get_logger().info(f"[Gesture NAV2_STOP] ok={ok}, msg={res_msg}")

    def image_callback(self, msg):
        now = time.time()
        self.last_camera_time = now
        self.last_pi5_time = now
        # FPS 측정
        self.fps_counter += 1
        if now - self.fps_start >= 1.0:
            self.current_fps = round(self.fps_counter / (now - self.fps_start), 1)
            self.fps_counter = 0
            self.fps_start = now
            with GlobalState.telemetry_lock:
                GlobalState.telemetry['tier3_laptop']['fps'] = self.current_fps

        # 원본 JPEG 패스스루 (서버 측 imdecode/박스 합성/imencode 제거 → CPU 0% & 원본 화질 유지)
        # YOLO 바운딩 박스는 브라우저 <canvas>가 /api/yolo 좌표를 받아 직접 렌더링
        if msg.data:
            with GlobalState.latest_jpeg_lock:
                GlobalState.latest_jpeg_bytes = bytes(msg.data)
                GlobalState.latest_jpeg_seq += 1

    def watchdog_callback(self):
        """1초 주기로 노드별 최종 수신 시간을 점검하여 3초 초과 시 NO_CONNECT 전환"""
        now = time.time()
        TIMEOUT = 3.0

        with GlobalState.telemetry_lock:
            # 1. Pi 5 연결 감시
            pi5_alive = (now - self.last_pi5_time < TIMEOUT) or (now - self.last_camera_time < TIMEOUT) or (now - self.last_lidar_time < TIMEOUT)
            if pi5_alive:
                GlobalState.telemetry['tier1_pi5']['status'] = 'ONLINE'
                if GlobalState.telemetry['tier1_pi5']['throttled'] == 'NO CONNECT':
                    GlobalState.telemetry['tier1_pi5']['throttled'] = '0x0 (무결성 유지)'
            else:
                GlobalState.telemetry['tier1_pi5']['status'] = 'NO_CONNECT'
                GlobalState.telemetry['tier1_pi5']['lidar_status'] = 'NO CONNECT (신호 없음)'
                GlobalState.telemetry['tier1_pi5']['throttled'] = 'NO CONNECT'

            # 2. OpenCR 연결 감시 (Pi 5가 죽으면 OpenCR도 자동 NO_CONNECT)
            opencr_alive = pi5_alive and (now - self.last_opencr_time < TIMEOUT)
            if opencr_alive:
                GlobalState.telemetry['tier1_opencr']['status'] = 'ONLINE'
            else:
                GlobalState.telemetry['tier1_opencr']['status'] = 'NO_CONNECT'
                GlobalState.telemetry['tier1_opencr']['voltage'] = 0.0
                GlobalState.telemetry['tier1_opencr']['is_switch_on'] = False
                GlobalState.telemetry['tier1_opencr']['torque'] = False
                GlobalState.telemetry['tier1_opencr']['alert'] = 'NO CONNECT (응답 없음)'

            # 3. Jetson Orin Nano 연결 감시
            jetson_alive = (now - self.last_jetson_time < TIMEOUT)
            if jetson_alive:
                GlobalState.telemetry['tier2_jetson']['status'] = 'ONLINE'
            else:
                GlobalState.telemetry['tier2_jetson']['status'] = 'NO_CONNECT'
                GlobalState.telemetry['tier2_jetson']['detected_objects'] = 'NO CONNECT'

            # 4. 카메라 스트림 FPS 초기화
            if now - self.last_camera_time > TIMEOUT:
                GlobalState.telemetry['tier3_laptop']['fps'] = 0.0

            # 5. 전체 종합 상태 판정
            if not pi5_alive and not jetson_alive:
                GlobalState.telemetry['status'] = 'NO_CONNECT'
                GlobalState.telemetry['summary'] = 'Pi 5 & Jetson NO CONNECT'
            elif not pi5_alive:
                GlobalState.telemetry['status'] = 'NO_CONNECT'
                GlobalState.telemetry['summary'] = 'Pi 5 NO CONNECT'
            elif not jetson_alive:
                GlobalState.telemetry['status'] = 'NO_CONNECT'
                GlobalState.telemetry['summary'] = 'Jetson NO CONNECT'
            elif not opencr_alive or not GlobalState.telemetry['tier1_opencr'].get('is_switch_on', False):
                GlobalState.telemetry['status'] = 'ATTENTION'
                GlobalState.telemetry['summary'] = '18650 스위치 확인'
            else:
                GlobalState.telemetry['status'] = 'ALL_GREEN'
                GlobalState.telemetry['summary'] = 'ALL GREEN'

            GlobalState.telemetry['timestamp'] = now


def start_web_server(port=9999):
    server = ThreadedHTTPServer(('0.0.0.0', port), WebHandler)
    server.serve_forever()


def main():
    port = 9999
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass

    # 1. 웹 서버 백그라운드 데몬 스레드 시작
    web_thread = threading.Thread(target=start_web_server, args=(port,), daemon=True)
    web_thread.start()

    # 2. ROS 2 노드 스핀
    rclpy.init()
    node = WebMonitorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()

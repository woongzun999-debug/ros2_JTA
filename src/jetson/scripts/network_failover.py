#!/usr/bin/env python3
"""
Jetson Network Failover Daemon
- Primary: Ethernet (enP8p1s0)
- Backup: WiFi (wlP1p1s0)
- Fixed IP: JTA_JETSON_IP (<JETSON_IP>/24)
- Gateway: JTA_GATEWAY_IP (<GATEWAY_IP>)
"""

import os
import sys
import time
import signal
import socket
import struct
import subprocess
import logging

PRIMARY_IFACE = "enP8p1s0"
BACKUP_IFACE = "wlP1p1s0"
TARGET_IP = os.environ.get("JTA_JETSON_IP", "<JETSON_IP>")
CIDR = "24"
GATEWAY = os.environ.get("JTA_GATEWAY_IP", "<GATEWAY_IP>")
DNS_SERVERS = ["168.126.63.1", "8.8.8.8"]
CHECK_INTERVAL = 0.5  # seconds
DEBOUNCE_TIME = 1.0   # seconds to confirm state change

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

running = True

def handle_signal(sig, frame):
    global running
    logging.info(f"Received signal {sig}, terminating gracefully...")
    running = False

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

def run_cmd(cmd_list, check=False):
    try:
        res = subprocess.run(cmd_list, capture_output=True, text=True, check=check)
        return res.returncode == 0, res.stdout.strip()
    except Exception as e:
        logging.error(f"Command failed {cmd_list}: {e}")
        return False, ""

def get_carrier(iface):
    path = f"/sys/class/net/{iface}/carrier"
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r") as f:
            return f.read().strip() == "1"
    except Exception:
        return False

def get_mac(iface):
    path = f"/sys/class/net/{iface}/address"
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return None

def has_ip(iface, ip):
    ok, out = run_cmd(["ip", "-4", "-brief", "addr", "show", "dev", iface])
    return f"{ip}/" in out

def send_garp(iface, ip):
    mac = get_mac(iface)
    if not mac:
        logging.warning(f"Could not read MAC for {iface}")
        return

    try:
        mac_bytes = bytes.fromhex(mac.replace(":", ""))
        ip_bytes = socket.inet_aton(ip)
        bcast = b"\xff" * 6

        # Ether header: Dest MAC (bcast) + Src MAC + Type (0x0806 ARP)
        eth_hdr = bcast + mac_bytes + struct.pack("!H", 0x0806)

        # Raw socket
        with socket.socket(socket.AF_PACKET, socket.SOCK_RAW) as s:
            s.bind((iface, 0))

            # 1) GARP Announcement (ARP Request: opcode 1, target MAC 00:00:00:00:00:00)
            arp_req = (
                struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1)
                + mac_bytes
                + ip_bytes
                + (b"\x00" * 6)
                + ip_bytes
            )
            pkt_req = eth_hdr + arp_req

            # 2) GARP Reply (ARP Reply: opcode 2, target MAC ff:ff:ff:ff:ff:ff)
            arp_reply = (
                struct.pack("!HHBBH", 1, 0x0800, 6, 4, 2)
                + mac_bytes
                + ip_bytes
                + bcast
                + ip_bytes
            )
            pkt_reply = eth_hdr + arp_reply

            # Broadcast multiple times to ensure switch/router update
            for _ in range(2):
                s.send(pkt_req)
                s.send(pkt_reply)
                time.sleep(0.05)

        logging.info(f"Sent Gratuitous ARP on {iface} (MAC {mac}, IP {ip})")
    except Exception as e:
        logging.warning(f"Error sending GARP packet on {iface}: {e}")

def switch_to_backup():
    logging.warning("=== [FAILOVER TRIGGERED] Primary Ethernet down! ===")
    logging.info(f"Transitioning {TARGET_IP} to WiFi ({BACKUP_IFACE})...")

    # Remove IP from downed Ethernet to avoid duplicate connected subnet route / DDS binding ambiguity
    if has_ip(PRIMARY_IFACE, TARGET_IP):
        run_cmd(["ip", "addr", "del", f"{TARGET_IP}/{CIDR}", "dev", PRIMARY_IFACE])

    # Add IP to WiFi
    if not has_ip(BACKUP_IFACE, TARGET_IP):
        run_cmd(["ip", "addr", "add", f"{TARGET_IP}/{CIDR}", "brd", "+", "dev", BACKUP_IFACE])

    # Replace Default Route
    run_cmd(["ip", "route", "replace", "default", "via", GATEWAY, "dev", BACKUP_IFACE, "metric", "50"])

    # Configure DNS via systemd-resolved
    run_cmd(["resolvectl", "dns", BACKUP_IFACE] + DNS_SERVERS)
    run_cmd(["resolvectl", "default-route", BACKUP_IFACE, "true"])

    # Gratuitous ARP
    send_garp(BACKUP_IFACE, TARGET_IP)

    logging.info(f"=== [FAILOVER COMPLETE] Active link is now WiFi ({BACKUP_IFACE}) ===")

def switch_to_primary():
    logging.info("=== [FAILBACK TRIGGERED] Primary Ethernet link restored! ===")
    logging.info(f"Transitioning {TARGET_IP} back to Ethernet ({PRIMARY_IFACE})...")

    # Remove IP from WiFi
    if has_ip(BACKUP_IFACE, TARGET_IP):
        run_cmd(["ip", "addr", "del", f"{TARGET_IP}/{CIDR}", "dev", BACKUP_IFACE])

    # Reset DNS on WiFi
    run_cmd(["resolvectl", "default-route", BACKUP_IFACE, "false"])

    # Ensure IP is on Ethernet
    if not has_ip(PRIMARY_IFACE, TARGET_IP):
        run_cmd(["ip", "addr", "add", f"{TARGET_IP}/{CIDR}", "brd", "+", "dev", PRIMARY_IFACE])

    # Replace Default Route
    run_cmd(["ip", "route", "replace", "default", "via", GATEWAY, "dev", PRIMARY_IFACE, "metric", "100"])

    # Gratuitous ARP
    send_garp(PRIMARY_IFACE, TARGET_IP)

    logging.info(f"=== [FAILBACK COMPLETE] Active link is now Ethernet ({PRIMARY_IFACE}) ===")

def main():
    logging.info("Starting Network Failover Daemon...")
    logging.info(f"Primary: {PRIMARY_IFACE}, Backup: {BACKUP_IFACE}, Target IP: {TARGET_IP}")

    primary_carrier = get_carrier(PRIMARY_IFACE)
    if primary_carrier:
        current_state = "primary"
        logging.info(f"Initial state: PRIMARY ({PRIMARY_IFACE} carrier UP)")
        # Make sure WiFi doesn't hold target IP from previous unclean shutdown
        if has_ip(BACKUP_IFACE, TARGET_IP):
            run_cmd(["ip", "addr", "del", f"{TARGET_IP}/{CIDR}", "dev", BACKUP_IFACE])
        if not has_ip(PRIMARY_IFACE, TARGET_IP):
            run_cmd(["ip", "addr", "add", f"{TARGET_IP}/{CIDR}", "brd", "+", "dev", PRIMARY_IFACE])
        run_cmd(["ip", "route", "replace", "default", "via", GATEWAY, "dev", PRIMARY_IFACE, "metric", "100"])
    else:
        current_state = "backup"
        logging.info(f"Initial state: BACKUP ({PRIMARY_IFACE} carrier DOWN)")
        switch_to_backup()

    last_change_time = 0
    pending_state = None

    while running:
        time.sleep(CHECK_INTERVAL)
        carrier = get_carrier(PRIMARY_IFACE)
        now = time.monotonic()

        target_state = "primary" if carrier else "backup"

        if target_state != current_state:
            if pending_state != target_state:
                pending_state = target_state
                last_change_time = now
            elif (now - last_change_time) >= DEBOUNCE_TIME:
                if target_state == "backup":
                    switch_to_backup()
                    current_state = "backup"
                else:
                    switch_to_primary()
                    current_state = "primary"
                pending_state = None
        else:
            pending_state = None

    logging.info("Network Failover Daemon stopped.")

if __name__ == "__main__":
    main()

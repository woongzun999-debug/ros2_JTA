#!/bin/bash
# ==============================================================================
# Jetson Orin Nano Display Rotation Helper (TUI fbcon & X11 GUI)
# Maintainer: ROS2_JW
# ==============================================================================

set -e

SYSFS_ROTATE="/sys/class/graphics/fbcon/rotate_all"
EXTLINUX_CONF="/boot/extlinux/extlinux.conf"

apply_rotation() {
    local val=$1
    local name=$2

    echo -e "\n🔄 화면 회전 적용 중: [\033[1;33m${name}\033[0m] (코드: ${val})"

    # 1. TUI (fbcon) 회전
    if [ -w "$SYSFS_ROTATE" ]; then
        echo "$val" > "$SYSFS_ROTATE"
    else
        echo "$val" | sudo tee "$SYSFS_ROTATE" > /dev/null
    fi
    echo -e "  ✅ TUI 콘솔(fbcon) 회전 완료"

    # 2. X11 GUI 회전 (혹시 Xorg/데스크톱이 떠 있을 경우 대비)
    local xrandr_rot="normal"
    case "$val" in
        0) xrandr_rot="normal" ;;
        1) xrandr_rot="right" ;;
        2) xrandr_rot="inverted" ;;
        3) xrandr_rot="left" ;;
    esac

    if [ -n "$DISPLAY" ] && command -v xrandr >/dev/null 2>&1; then
        xrandr -o "$xrandr_rot" 2>/dev/null && echo -e "  ✅ X11 데스크톱(xrandr) 회전 완료" || true
    fi
}

make_permanent() {
    local val=$1
    echo -e "\n💾 부팅 시 영구 적용 설정 중 (/boot/extlinux/extlinux.conf)..."
    
    if [ ! -f "$EXTLINUX_CONF" ]; then
        echo -e "  ❌ $EXTLINUX_CONF 파일을 찾을 수 없습니다."
        return 1
    fi

    # 백업 생성
    sudo cp "$EXTLINUX_CONF" "${EXTLINUX_CONF}.bak.$(date +%Y%m%d_%H%M%S)"

    # 기존 fbcon=rotate:* 제거
    sudo sed -i 's/ fbcon=rotate:[0-3]//g' "$EXTLINUX_CONF"

    # 0이 아닐 경우에만 추가
    if [ "$val" -ne 0 ]; then
        sudo sed -i "s/fbcon=map:0/fbcon=map:0 fbcon=rotate:${val}/g" "$EXTLINUX_CONF"
        echo -e "  ✅ [fbcon=rotate:${val}] 영구 등록 완료"
    else
        echo -e "  ✅ 기본값(0도)으로 영구 설정 완료"
    fi
}

show_menu() {
    while true; do
        current=$(cat /sys/class/graphics/fbcon/rotate 2>/dev/null || echo "0")
        echo -e "\n=============================================="
        echo -e " 🖥️  Jetson Orin Nano 화면 회전 관리자 (ROS2_JW)"
        echo -e "=============================================="
        echo -e " 현재 회전 상태: \033[1;36m${current}\033[0m (0:기본, 1:시계90°, 2:180°, 3:반시계90°)"
        echo -e "----------------------------------------------"
        echo -e " 1) 90도 회전 (시계 방향 / 세로 모드)"
        echo -e " 2) 270도 회전 (반시계 방향 / 세로 모드)"
        echo -e " 3) 0도 회전 (기본 가로 모드 원복)"
        echo -e " 4) 180도 회전 (상하 반전)"
        echo -e " 5) 현재 설정을 부팅 시 영구 적용으로 저장"
        echo -e " q) 종료"
        echo -e "----------------------------------------------"
        read -rp "선택할 번호를 입력하세요 [1-5, q]: " choice

        case "$choice" in
            1) apply_rotation 1 "90도 (시계 방향)" ;;
            2) apply_rotation 3 "270도 (반시계 방향)" ;;
            3) apply_rotation 0 "0도 (기본 가로)" ;;
            4) apply_rotation 2 "180도 (상하 반전)" ;;
            5) make_permanent "$current" ;;
            q|Q) echo "종료합니다."; break ;;
            *) echo "올바른 번호를 선택해주세요." ;;
        esac
    done
}

# 인자 처리
if [ $# -gt 0 ]; then
    arg="$1"
    permanent=false
    if [ "$1" == "--permanent" ] || [ "$1" == "-p" ]; then
        permanent=true
        arg="$2"
    fi

    case "$arg" in
        90|right|1)
            apply_rotation 1 "90도 (시계 방향)"
            [ "$permanent" = true ] && make_permanent 1
            ;;
        270|left|3)
            apply_rotation 3 "270도 (반시계 방향)"
            [ "$permanent" = true ] && make_permanent 3
            ;;
        0|normal|reset)
            apply_rotation 0 "0도 (기본 가로)"
            [ "$permanent" = true ] && make_permanent 0
            ;;
        180|inverted|2)
            apply_rotation 2 "180도 (상하 반전)"
            [ "$permanent" = true ] && make_permanent 2
            ;;
        *)
            echo "사용법: $0 [0|90|180|270] [--permanent]"
            echo "또는 인자 없이 실행하여 대화형 메뉴 사용"
            exit 1
            ;;
    esac
else
    show_menu
fi

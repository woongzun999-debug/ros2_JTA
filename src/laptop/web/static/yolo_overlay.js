/**
 * yolo_overlay.js — 브라우저 <canvas> 기반 YOLO 바운딩 박스 오버레이 렌더러
 *
 * - 서버(/video_feed)가 원본 JPEG를 패스스루로 전송하고,
 * - 브라우저가 /api/yolo에서 경량 JSON 좌표만 받아 object-contain 영역에 맞춰 직접 렌더링합니다.
 */
(function (global) {
  'use strict';

  // 기존 OpenCV BGR 팔레트와 1:1 매칭되는 RGB 색상
  const COLOR_PALETTE = [
    'rgb(128, 255, 0)',   // 에메랄드/라임 그린
    'rgb(255, 215, 0)',   // 골드/옐로우
    'rgb(30, 144, 255)',  // 일렉트릭 블루
    'rgb(255, 165, 0)',   // 오렌지
    'rgb(255, 0, 255)',   // 마젠타
    'rgb(255, 255, 0)',   // 옐로우
  ];

  const BOX_SHRINK_SCALE = 0.92;

  function computeContainedRect(containerW, containerH, srcW, srcH) {
    if (containerW <= 0 || containerH <= 0 || srcW <= 0 || srcH <= 0) {
      return { x: 0, y: 0, w: 0, h: 0 };
    }
    const containerRatio = containerW / containerH;
    const srcRatio = srcW / srcH;
    let drawW, drawH, drawX, drawY;
    if (srcRatio > containerRatio) {
      drawW = containerW;
      drawH = containerW / srcRatio;
      drawX = 0;
      drawY = (containerH - drawH) * 0.5;
    } else {
      drawH = containerH;
      drawW = containerH * srcRatio;
      drawX = (containerW - drawW) * 0.5;
      drawY = 0;
    }
    return { x: drawX, y: drawY, w: drawW, h: drawH };
  }

  function attachYoloOverlay(options) {
    const img = document.getElementById(options.imgId);
    const canvas = document.getElementById(options.canvasId);
    if (!img || !canvas) return null;

    const ctx = canvas.getContext('2d');
    const pollMs = options.pollMs || 120;
    const apiUrl = options.apiUrl || '/api/yolo';

    let latestData = { image_size: [640, 480], objects: [], fresh: false };
    let inFlight = false;
    let timerId = null;

    function syncCanvasSize() {
      const rect = canvas.getBoundingClientRect();
      const dpr = global.devicePixelRatio || 1;
      const targetW = Math.max(1, Math.round(rect.width * dpr));
      const targetH = Math.max(1, Math.round(rect.height * dpr));
      if (canvas.width !== targetW || canvas.height !== targetH) {
        canvas.width = targetW;
        canvas.height = targetH;
      }
      return { cssW: rect.width, cssH: rect.height, dpr };
    }

    function render() {
      const { cssW, cssH, dpr } = syncCanvasSize();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cssW, cssH);

      if (!latestData) {
        return;
      }

      const imgSize = Array.isArray(latestData.image_size) && latestData.image_size.length === 2
        ? latestData.image_size
        : [640, 480];
      const srcW = Number(imgSize[0]) || img.naturalWidth || 640;
      const srcH = Number(imgSize[1]) || img.naturalHeight || 480;

      const natW = img.naturalWidth || srcW;
      const natH = img.naturalHeight || srcH;
      const draw = computeContainedRect(cssW, cssH, natW, natH);
      if (draw.w <= 0 || draw.h <= 0) return;

      const hasObjects = Boolean(latestData.fresh && Array.isArray(latestData.objects) && latestData.objects.length > 0);
      const scaleX = draw.w / srcW;
      const scaleY = draw.h / srcH;
      const isCompact = draw.w < 300;
      const fontSize = isCompact ? 10 : 12;
      const lineWidth = isCompact ? 1.5 : 2;
      const crossHalf = isCompact ? 4 : 6;

      ctx.font = `bold ${fontSize}px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace`;
      ctx.textBaseline = 'top';

      // 0) 전 구간 상시 바닥 장애물 감지 오버레이 (ROI 경계선 + 장애물 박스 + 좌측 상단 배지)
      const flr = latestData.floor_obstacles;
      if (flr && flr.active) {
        const roiRatio = Number(flr.roi_top_ratio);
        if (Number.isFinite(roiRatio) && roiRatio > 0 && roiRatio < 1) {
          const roiY = draw.y + draw.h * roiRatio;
          ctx.save();
          ctx.strokeStyle = 'rgba(52, 211, 153, 0.55)';
          ctx.lineWidth = 1;
          ctx.setLineDash([6, 4]);
          ctx.beginPath();
          ctx.moveTo(draw.x, roiY);
          ctx.lineTo(draw.x + draw.w, roiY);
          ctx.stroke();
          ctx.restore();
        }

        const fBoxes = Array.isArray(flr.boxes) ? flr.boxes : [];
        for (const item of fBoxes) {
          const b = item.box;
          if (!Array.isArray(b) || b.length !== 4) continue;
          const fx1 = Math.max(draw.x + 1, Math.min(draw.x + draw.w - 2, draw.x + Number(b[0]) * scaleX));
          const fy1 = Math.max(draw.y + 1, Math.min(draw.y + draw.h - 2, draw.y + Number(b[1]) * scaleY));
          const fx2 = Math.max(draw.x + 1, Math.min(draw.x + draw.w - 2, draw.x + Number(b[2]) * scaleX));
          const fy2 = Math.max(draw.y + 1, Math.min(draw.y + draw.h - 2, draw.y + Number(b[3]) * scaleY));
          const fw = Math.max(0, fx2 - fx1);
          const fh = Math.max(0, fy2 - fy1);
          if (fw < 2 || fh < 2) continue;

          ctx.fillStyle = 'rgba(244, 63, 94, 0.16)';
          ctx.fillRect(fx1, fy1, fw, fh);
          ctx.strokeStyle = 'rgba(244, 63, 94, 0.95)';
          ctx.lineWidth = lineWidth;
          ctx.strokeRect(fx1, fy1, fw, fh);

          const distNum = Number(item.dist);
          const fLabel = Number.isFinite(distNum) && distNum > 0
            ? `바닥 ${distNum.toFixed(2)}m`
            : '바닥장애물';
          const padX = isCompact ? 3 : 4;
          const padY = 2;
          const tw = ctx.measureText(fLabel).width;
          const lbW = tw + padX * 2;
          const lbH = fontSize + padY * 2;
          let lx = fx1;
          if (lx + lbW > draw.x + draw.w - 2) lx = Math.max(draw.x + 2, draw.x + draw.w - lbW - 2);
          let ly = fy1 - lbH;
          if (ly < draw.y + 2) ly = fy1;

          ctx.fillStyle = 'rgba(225, 29, 72, 0.92)';
          ctx.fillRect(lx, ly, lbW, lbH);
          ctx.fillStyle = '#ffffff';
          ctx.fillText(fLabel, lx + padX, ly + padY);
        }

        if (Number(flr.confirmed_beams) > 0) {
          const md = Number(flr.min_dist);
          const badge = Number.isFinite(md) && md > 0
            ? `⚠️ 바닥장애물 ${md.toFixed(2)}m`
            : '⚠️ 바닥장애물 감지';
          const bFont = isCompact ? 10 : 12;
          ctx.save();
          ctx.font = `bold ${bFont}px ui-sans-serif, system-ui, -apple-system, sans-serif`;
          const padX = isCompact ? 6 : 8;
          const padY = isCompact ? 4 : 5;
          const tw = ctx.measureText(badge).width;
          const bw = tw + padX * 2;
          const bh = bFont + padY * 2;
          const bx = draw.x + 8;
          const by = draw.y + 8;
          ctx.fillStyle = 'rgba(190, 18, 60, 0.9)';
          ctx.fillRect(bx, by, bw, bh);
          ctx.strokeStyle = '#fda4af';
          ctx.lineWidth = 1.5;
          ctx.strokeRect(bx, by, bw, bh);
          ctx.fillStyle = '#fff1f2';
          ctx.fillText(badge, bx + padX, by + padY);
          ctx.restore();
          ctx.font = `bold ${fontSize}px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace`;
        }
      }

      if (hasObjects) for (const obj of latestData.objects) {
        const box = obj.box || obj.bbox;
        if (!Array.isArray(box) || box.length !== 4) continue;

        let x1 = Number(box[0]);
        let y1 = Number(box[1]);
        let x2 = Number(box[2]);
        let y2 = Number(box[3]);
        if (![x1, y1, x2, y2].every(Number.isFinite)) continue;

        // 중심점 기준 8% 축소 (기존 서버 렌더링과 동일)
        const bw = x2 - x1;
        const bh = y2 - y1;
        const cx = x1 + bw * 0.5;
        const cy = y1 + bh * 0.5;
        x1 = cx - (bw * BOX_SHRINK_SCALE) * 0.5;
        x2 = cx + (bw * BOX_SHRINK_SCALE) * 0.5;
        y1 = cy - (bh * BOX_SHRINK_SCALE) * 0.5;
        y2 = cy + (bh * BOX_SHRINK_SCALE) * 0.5;

        // 소스 이미지 좌표 → 캔버스 CSS 픽셀 좌표 변환 & 경계 클램핑
        const rx1 = Math.max(draw.x + 1, Math.min(draw.x + draw.w - 2, draw.x + x1 * scaleX));
        const ry1 = Math.max(draw.y + 1, Math.min(draw.y + draw.h - 2, draw.y + y1 * scaleY));
        const rx2 = Math.max(draw.x + 1, Math.min(draw.x + draw.w - 2, draw.x + x2 * scaleX));
        const ry2 = Math.max(draw.y + 1, Math.min(draw.y + draw.h - 2, draw.y + y2 * scaleY));
        const rw = Math.max(0, rx2 - rx1);
        const rh = Math.max(0, ry2 - ry1);

        const clsId = Number.isFinite(Number(obj.class_id)) ? Math.abs(Math.trunc(Number(obj.class_id))) : 0;
        const color = COLOR_PALETTE[clsId % COLOR_PALETTE.length];

        // 1) 바운딩 박스
        ctx.strokeStyle = color;
        ctx.lineWidth = lineWidth;
        ctx.strokeRect(rx1, ry1, rw, rh);

        // 2) 중심점 십자(+) 마커
        if (Array.isArray(obj.center) && obj.center.length === 2) {
          const mcx = Math.max(draw.x + 2, Math.min(draw.x + draw.w - 2, draw.x + Number(obj.center[0]) * scaleX));
          const mcy = Math.max(draw.y + 2, Math.min(draw.y + draw.h - 2, draw.y + Number(obj.center[1]) * scaleY));
          ctx.beginPath();
          ctx.moveTo(mcx - crossHalf, mcy);
          ctx.lineTo(mcx + crossHalf, mcy);
          ctx.moveTo(mcx, mcy - crossHalf);
          ctx.lineTo(mcx, mcy + crossHalf);
          ctx.stroke();
        }

        // 3) 상단 라벨 박스 & 텍스트 (상단/우측 탈출 방지 스마트 플립)
        const clsName = obj.class_name || 'obj';
        const confVal = Number(obj.conf !== undefined ? obj.conf : (obj.confidence || 0));
        const label = `${clsName} ${confVal.toFixed(2)}`;
        const padX = isCompact ? 3 : 4;
        const padY = 2;
        const textW = ctx.measureText(label).width;
        const boxW = textW + padX * 2;
        const boxH = fontSize + padY * 2;

        let lx = rx1;
        if (lx + boxW > draw.x + draw.w - 2) {
          lx = Math.max(draw.x + 2, draw.x + draw.w - boxW - 2);
        }
        let ly = ry1 - boxH;
        if (ly < draw.y + 2) {
          ly = ry1;
        }

        ctx.fillStyle = color;
        ctx.fillRect(lx, ly, boxW, boxH);
        ctx.fillStyle = '#000000';
        ctx.fillText(label, lx + padX, ly + padY);
      }

      // 4) ArUco 숫자 카드 실시간 HUD 배지 (우측 상단)
      const g = latestData.gesture;
      if (g && (g.confirmed || g.active)) {
        let badgeText = '';
        let bg = 'rgba(15, 23, 42, 0.82)';
        let border = '#38bdf8';
        let progress = 0;

        if (g.confirmed) {
          if (g.cmd === 'ESTOP') {
            badgeText = '🛑 STOP 카드 비상정지!';
            bg = 'rgba(185, 28, 28, 0.92)';
            border = '#fca5a5';
          } else if (g.cmd === 'GOTO_WP') {
            badgeText = `✅ 카드 WP ${g.cmd_wp} 이동!`;
            bg = 'rgba(4, 120, 87, 0.92)';
            border = '#6ee7b7';
          } else if (g.cmd === 'NAV2_STOP') {
            badgeText = '✋ Nav2 종료 실행!';
            bg = 'rgba(185, 28, 28, 0.92)';
            border = '#fca5a5';
          } else if (g.cmd === 'NAV2_START') {
            badgeText = '👍 Nav2+자동순찰 시작!';
            bg = 'rgba(4, 120, 87, 0.92)';
            border = '#6ee7b7';
          }
          progress = 1;
        } else if (g.active) {
          const hold = Number(g.hold_sec) || 0;
          const reqHold = Number(g.req_hold_sec) || (g.candidate === 'ESTOP' ? 0.25 : 0.8);
          if (g.candidate === 'ESTOP') {
            badgeText = `🛑 STOP 카드 (${hold.toFixed(2)}s)`;
            bg = 'rgba(153, 27, 27, 0.85)';
            border = '#f87171';
            progress = Math.min(1, hold / reqHold);
          } else if (g.candidate === 'GOTO_WP') {
            badgeText = `🪪 WP ${g.wp_index} 카드 (${hold.toFixed(1)}/${reqHold.toFixed(1)}s)`;
            bg = 'rgba(15, 23, 42, 0.85)';
            border = '#38bdf8';
            progress = Math.min(1, hold / reqHold);
          } else if (g.candidate === 'NAV2_STOP') {
            badgeText = `✋ Nav2 종료 (${hold.toFixed(1)}/${reqHold.toFixed(1)}s)`;
            bg = 'rgba(153, 27, 27, 0.85)';
            border = '#f87171';
            progress = Math.min(1, hold / reqHold);
          } else if (g.candidate === 'NAV2_START') {
            badgeText = `👍 Nav2+순찰 (${hold.toFixed(1)}/${reqHold.toFixed(1)}s)`;
            bg = 'rgba(6, 95, 70, 0.85)';
            border = '#34d399';
            progress = Math.min(1, hold / reqHold);
          }
        }

        if (badgeText) {
          const gFont = isCompact ? 10 : 12;
          ctx.font = `bold ${gFont}px ui-sans-serif, system-ui, -apple-system, sans-serif`;
          const padX = isCompact ? 6 : 8;
          const padY = isCompact ? 4 : 5;
          const tw = ctx.measureText(badgeText).width;
          const bw = tw + padX * 2;
          const bh = gFont + padY * 2 + 4;
          const bx = Math.max(draw.x + 6, draw.x + draw.w - bw - 8);
          const by = draw.y + 8;

          ctx.fillStyle = bg;
          ctx.fillRect(bx, by, bw, bh);
          ctx.strokeStyle = border;
          ctx.lineWidth = 1.5;
          ctx.strokeRect(bx, by, bw, bh);

          // 하단 홀드 진행 바
          if (progress > 0) {
            ctx.fillStyle = border;
            ctx.fillRect(bx, by + bh - 3, bw * progress, 3);
          }

          ctx.fillStyle = '#f8fafc';
          ctx.fillText(badgeText, bx + padX, by + padY);
        }
      }

      // 5) 근접 사람 감지 카운트다운 / 추종 / 쿨다운 HUD 배지 (상단 중앙)
      const pc = latestData.person_countdown;
      if (pc && pc.state && pc.state !== 'IDLE') {
        const rem = Math.max(0, Number(pc.remaining_sec) || 0);
        const total = Math.max(0.1, Number(pc.total_sec) || 3.0);
        let pText = '';
        let pBg = 'rgba(15, 23, 42, 0.85)';
        let pBorder = '#f59e0b';
        let pProgress = 0;

        if (pc.state === 'DETECTING') {
          pText = `👤 사람 감지 ${rem.toFixed(1)}s`;
          pBg = 'rgba(180, 83, 9, 0.90)';
          pBorder = '#fbbf24';
          pProgress = Math.max(0, Math.min(1, (total - rem) / total));
        } else if (pc.state === 'FOLLOWING') {
          pText = `🏃 사람 추종 ${Math.ceil(rem)}s`;
          pBg = 'rgba(4, 120, 87, 0.90)';
          pBorder = '#34d399';
          pProgress = Math.max(0, Math.min(1, rem / total));
        } else if (pc.state === 'COOLDOWN') {
          pText = `⏳ 재감지 대기 ${Math.ceil(rem)}s`;
          pBg = 'rgba(30, 41, 59, 0.82)';
          pBorder = '#94a3b8';
          pProgress = Math.max(0, Math.min(1, rem / total));
        }

        if (pText) {
          const pFont = isCompact ? 10 : 12;
          ctx.save();
          ctx.font = `bold ${pFont}px ui-sans-serif, system-ui, -apple-system, sans-serif`;
          const padX = isCompact ? 6 : 10;
          const padY = isCompact ? 4 : 5;
          const tw = ctx.measureText(pText).width;
          const bw = tw + padX * 2;
          const bh = pFont + padY * 2 + 4;
          const bx = Math.max(draw.x + 6, draw.x + (draw.w - bw) * 0.5);
          const by = draw.y + 8;

          ctx.fillStyle = pBg;
          ctx.fillRect(bx, by, bw, bh);
          ctx.strokeStyle = pBorder;
          ctx.lineWidth = 1.5;
          ctx.strokeRect(bx, by, bw, bh);

          if (pProgress > 0) {
            ctx.fillStyle = pBorder;
            ctx.fillRect(bx, by + bh - 3, bw * pProgress, 3);
          }

          ctx.fillStyle = '#f8fafc';
          ctx.fillText(pText, bx + padX, by + padY);
          ctx.restore();
        }
      }
    }

    async function pollOnce() {
      if (inFlight || document.visibilityState === 'hidden') return;
      inFlight = true;
      try {
        const res = await fetch(apiUrl, { cache: 'no-store' });
        if (res.ok) {
          latestData = await res.json();
          render();
        }
      } catch (_) {
        // 네트워크 일시 끊김 시 기존 프레임 유지
      } finally {
        inFlight = false;
      }
    }

    const resizeObserver = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(() => render())
      : null;
    if (resizeObserver) resizeObserver.observe(canvas);
    global.addEventListener('resize', render);
    img.addEventListener('load', render);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') pollOnce();
    });

    timerId = global.setInterval(pollOnce, pollMs);
    pollOnce();

    return {
      stop() {
        if (timerId !== null) global.clearInterval(timerId);
        if (resizeObserver) resizeObserver.disconnect();
        global.removeEventListener('resize', render);
      },
      render,
    };
  }

  global.attachYoloOverlay = attachYoloOverlay;
})(window);

import { useEffect, useRef, useState } from "react";
import { CAT_COLOR, Frame, RF } from "./api";

export const BEAM_COLOR: Record<string, string> = { L: "#7EA6E0", C: "#8CC79A", R: "#C3A1EC" };

export type Layers = { rejected: boolean; filtered: boolean; clusters: boolean; tracks: boolean; labels: boolean; trails: boolean };
export const DEFAULT_LAYERS: Layers = { rejected: false, filtered: true, clusters: true, tracks: true, labels: true, trails: true };

type Props = {
  frameRef: React.MutableRefObject<Frame | null>;
  rfRef?: React.MutableRefObject<RF | null>;
  rangeMax: number;
  layers: Layers;
  selected?: number | null;
  onSelect?: (trackId: number | null) => void;
  resetKey?: number;
  compact?: boolean;
};

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** 2D polar plot of a single-plane scan. World: metres, +x right, +y up, sensor at origin. */
export default function ScanCanvas({ frameRef, rfRef, rangeMax, layers, selected, onSelect, resetKey, compact }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const view = useRef({ zoom: 1, px: 0, py: 0 });
  const props = useRef({ rangeMax, layers, selected });
  props.current = { rangeMax, layers, selected };
  const [hover, setHover] = useState<{ x: number; y: number } | null>(null);
  const hoverRef = useRef(hover);
  hoverRef.current = hover;

  useEffect(() => { view.current = { zoom: 1, px: 0, py: 0 }; }, [resetKey]);

  useEffect(() => {
    const cv = ref.current!;
    const ctx = cv.getContext("2d")!;
    let raf = 0, colors = readColors(), themeTick = 0;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      if (++themeTick % 60 === 0) colors = readColors();
      const dpr = window.devicePixelRatio || 1;
      const w = cv.clientWidth, h = cv.clientHeight;
      if (cv.width !== w * dpr || cv.height !== h * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const { rangeMax, layers, selected } = props.current;
      const v = view.current;
      const scale = (Math.min(w, h) / 2 - (compact ? 14 : 34)) / rangeMax * v.zoom;
      const cx = w / 2, cy = h / 2;
      const sx = (x: number) => cx + (x - v.px) * scale, sy = (y: number) => cy - (y - v.py) * scale;
      const ox = sx(0), oy = sy(0);

      // grid: range rings + bearing spokes
      const step = rangeMax <= 4 ? 0.5 : rangeMax <= 8 ? 1 : 2;
      ctx.lineWidth = 1;
      ctx.font = `10.5px ${css("--mono") || "monospace"}`;
      for (let r = step; r <= rangeMax + 1e-6; r += step) {
        const major = Math.abs(r % (step * 2)) < 1e-6 || r === rangeMax;
        ctx.strokeStyle = major ? colors.grid2 : colors.grid;
        ctx.beginPath(); ctx.arc(ox, oy, r * scale, 0, Math.PI * 2); ctx.stroke();
        if (major && !compact) { ctx.fillStyle = colors.faint; ctx.fillText(`${r} m`, ox + 4, oy - r * scale - 4); }
      }
      for (let a = 0; a < 360; a += 30) {
        const t = (a * Math.PI) / 180, R = rangeMax * scale;
        ctx.strokeStyle = a % 90 === 0 ? colors.grid2 : colors.grid;
        ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(ox + Math.cos(t) * R, oy - Math.sin(t) * R); ctx.stroke();
        if (!compact) {
          ctx.fillStyle = colors.faint;
          ctx.textAlign = "center";
          ctx.fillText(`${a}°`, ox + Math.cos(t) * (R + 16), oy - Math.sin(t) * (R + 16) + 4);
          ctx.textAlign = "left";
        }
      }

      const f = frameRef.current;
      if (f) {
        const ps = Math.max(1.6, Math.min(3, scale / 70));
        if (layers.rejected) {
          ctx.fillStyle = colors.rejected;
          for (const [x, y] of f.rejected) ctx.fillRect(sx(x) - 1, sy(y) - 1, 2, 2);
        }
        for (const [x, y, k] of f.points) {
          if (k === 0 && !layers.filtered) continue;
          ctx.fillStyle = k === 0 ? colors.point : k === 1 || !layers.clusters ? colors.fg : colors.cluster;
          ctx.fillRect(sx(x) - ps / 2, sy(y) - ps / 2, ps, ps);
        }
        if (layers.clusters) {
          ctx.setLineDash([3, 3]); ctx.strokeStyle = colors.faint;
          for (const c of f.clusters) {
            ctx.strokeRect(sx(c.xmin) - 4, sy(c.ymax) - 4, (c.xmax - c.xmin) * scale + 8, (c.ymax - c.ymin) * scale + 8);
          }
          ctx.setLineDash([]);
        }
        if (layers.tracks) {
          for (const t of f.tracks) {
            const col = CAT_COLOR[t.category] ?? colors.fg;
            const sel = t.id === selected;
            if (layers.trails && t.trail.length > 1) {
              ctx.strokeStyle = col; ctx.globalAlpha = 0.45; ctx.lineWidth = 1.5;
              ctx.beginPath(); t.trail.forEach(([x, y], i) => (i ? ctx.lineTo(sx(x), sy(y)) : ctx.moveTo(sx(x), sy(y)))); ctx.stroke();
              ctx.globalAlpha = 1;
            }
            const [x0, y0, x1, y1] = t.box;
            ctx.strokeStyle = col; ctx.lineWidth = sel ? 2.5 : 1.5;
            roundRect(ctx, sx(x0) - 6, sy(y1) - 6, (x1 - x0) * scale + 12, (y1 - y0) * scale + 12, 6);
            ctx.stroke();
            if (t.risk !== "CLEAR") {
              ctx.fillStyle = t.risk === "WARNING" ? colors.bad : colors.warn;
              ctx.beginPath(); ctx.arc(sx(x1) + 6, sy(y1) - 6, 3.5, 0, Math.PI * 2); ctx.fill();
            }
            if (t.heading != null && t.speed != null) {
              const hr = (t.heading * Math.PI) / 180, L = 14 + Math.min(t.speed, 15) * 3;
              ctx.beginPath(); ctx.moveTo(sx(t.x), sy(t.y)); ctx.lineTo(sx(t.x) + Math.cos(hr) * L, sy(t.y) - Math.sin(hr) * L); ctx.stroke();
            }
            if (layers.labels) {
              const label = `#${t.id} ${t.category}  ${t.range.toFixed(1)} m`;
              ctx.font = `600 11px ${css("--font")}`;
              const tw = ctx.measureText(label).width;
              const lx = sx(x0) - 6, ly = sy(y1) - 26;
              ctx.fillStyle = colors.labelBg; roundRect(ctx, lx, ly, tw + 12, 18, 5); ctx.fill();
              ctx.fillStyle = colors.text; ctx.fillText(label, lx + 6, ly + 13);
            }
          }
        }
      }
      // the three single-beam range finders on the Pico W
      const rf = rfRef?.current;
      if (rf && rf.state !== "disabled") {
        for (const b of rf.beams) {
          const t = (b.angle * Math.PI) / 180, col = BEAM_COLOR[b.side];
          const len = (b.m ?? rangeMax) * scale;
          ctx.strokeStyle = col; ctx.lineWidth = b.m != null ? 2 : 1;
          ctx.globalAlpha = b.m != null ? 0.9 : 0.3; ctx.setLineDash(b.m != null ? [] : [3, 6]);
          ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(ox + Math.cos(t) * len, oy - Math.sin(t) * len); ctx.stroke();
          ctx.setLineDash([]); ctx.globalAlpha = 1;
          if (b.m != null) {
            const ex = ox + Math.cos(t) * len, ey = oy - Math.sin(t) * len;
            ctx.fillStyle = col; ctx.beginPath(); ctx.arc(ex, ey, 4, 0, Math.PI * 2); ctx.fill();
            if (!compact) { ctx.font = `600 11px ${css("--mono")}`; ctx.fillText(`${b.side} ${b.m.toFixed(2)} m`, ex + 7, ey - 7); }
          }
        }
      }
      // sensor
      ctx.fillStyle = colors.accent;
      ctx.beginPath(); ctx.arc(ox, oy, 5, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = colors.accent; ctx.globalAlpha = 0.35; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(ox, oy, 10, 0, Math.PI * 2); ctx.stroke(); ctx.globalAlpha = 1;
      const hv = hoverRef.current;
      if (hv && !compact) {
        const wx = (hv.x - cx) / scale + v.px, wy = -(hv.y - cy) / scale + v.py;
        const r = Math.hypot(wx, wy), b = (Math.atan2(wy, wx) * 180) / Math.PI;
        ctx.font = `11px ${css("--mono")}`; ctx.fillStyle = colors.muted;
        ctx.fillText(`x ${wx.toFixed(2)}  y ${wy.toFixed(2)}  r ${r.toFixed(2)} m  θ ${b.toFixed(1)}°`, 14, h - 34);
      }
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, [frameRef, rfRef, compact]);

  // zoom + pan + select
  useEffect(() => {
    const cv = ref.current!;
    let drag: { x: number; y: number; px: number; py: number; moved: boolean } | null = null;
    const scaleNow = () => (Math.min(cv.clientWidth, cv.clientHeight) / 2 - (compact ? 14 : 34)) / props.current.rangeMax * view.current.zoom;
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      view.current.zoom = Math.min(40, Math.max(0.5, view.current.zoom * Math.exp(-e.deltaY * 0.0015)));
    };
    const down = (e: PointerEvent) => { drag = { x: e.clientX, y: e.clientY, px: view.current.px, py: view.current.py, moved: false }; cv.setPointerCapture(e.pointerId); };
    const move = (e: PointerEvent) => {
      const r = cv.getBoundingClientRect();
      setHover({ x: e.clientX - r.left, y: e.clientY - r.top });
      if (!drag) return;
      const s = scaleNow();
      if (Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > 3) drag.moved = true;
      view.current.px = drag.px - (e.clientX - drag.x) / s;
      view.current.py = drag.py + (e.clientY - drag.y) / s;
    };
    const up = (e: PointerEvent) => {
      if (drag && !drag.moved && onSelect) {
        const r = cv.getBoundingClientRect(), s = scaleNow(), v = view.current;
        const wx = (e.clientX - r.left - cv.clientWidth / 2) / s + v.px, wy = -(e.clientY - r.top - cv.clientHeight / 2) / s + v.py;
        const hit = frameRef.current?.tracks.find((t) => wx >= t.box[0] - 0.3 && wx <= t.box[2] + 0.3 && wy >= t.box[1] - 0.3 && wy <= t.box[3] + 0.3);
        onSelect(hit ? hit.id : null);
      }
      drag = null;
    };
    const leave = () => setHover(null);
    cv.addEventListener("wheel", wheel, { passive: false });
    cv.addEventListener("pointerdown", down); cv.addEventListener("pointermove", move);
    cv.addEventListener("pointerup", up); cv.addEventListener("pointerleave", leave);
    return () => {
      cv.removeEventListener("wheel", wheel); cv.removeEventListener("pointerdown", down);
      cv.removeEventListener("pointermove", move); cv.removeEventListener("pointerup", up); cv.removeEventListener("pointerleave", leave);
    };
  }, [frameRef, onSelect, compact]);

  return <canvas ref={ref} aria-label="360 degree LiDAR scan, top-down view" role="img" />;
}

function readColors() {
  return {
    grid: css("--grid"), grid2: css("--grid-2"), faint: css("--faint"), muted: css("--muted"), text: css("--text"),
    accent: css("--accent-hi"), point: css("--faint"), fg: css("--accent-hi"), cluster: css("--text"),
    rejected: "rgba(163,79,54,.55)", warn: css("--warn"), bad: css("--bad"), labelBg: css("--glass"),
  };
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

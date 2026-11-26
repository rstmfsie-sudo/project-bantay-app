import { useEffect, useRef } from "react";

/** Tiny isometric 3D renderer (Canvas 2D, no libraries) for simple primitives in metres (x, y ground; z up).
 * Used for the digital-twin object models and the sensor rig. It draws MODELS, never sensor data. */
export type Prim =
  | { type: "box"; x: [number, number]; y: [number, number]; z: [number, number]; color?: string; yaw?: number }
  | { type: "lens"; c: [number, number, number]; r: number; yaw: number; color?: string }
  | { type: "wheel"; c: [number, number, number]; r: number; w: number; rim: number; color?: string }
  | { type: "cyl"; c: [number, number, number]; r: number; h: number; color?: string };
export type Beam = { from: [number, number, number]; angle: number; length: number; live: boolean; label: string; color: string };
type Rect = [number, number, number, number];

type Props = {
  prims: Prim[]; slices?: Rect[]; sliceZ?: number; beams?: Beam[];
  height?: number; spin?: boolean; yaw?: number; color?: string;
};

const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

function shade(hex: string, f: number) {
  const n = parseInt(hex.replace("#", ""), 16);
  const c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => Math.round(Math.min(255, v * f)));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

export default function Iso3D({ prims, slices = [], sliceZ, beams = [], height = 220, spin = true, yaw = 35, color = "#C96F4A" }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const data = useRef({ prims, slices, sliceZ, beams, color });
  data.current = { prims, slices, sliceZ, beams, color };

  useEffect(() => {
    const cv = ref.current!, ctx = cv.getContext("2d")!;
    let raf = 0, a = (yaw * Math.PI) / 180, last = performance.now();
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const draw = (now: number) => {
      raf = requestAnimationFrame(draw);
      if (spin && !reduce) a += ((now - last) / 1000) * 0.35;
      last = now;
      const { prims, slices, sliceZ, beams, color } = data.current;
      const dpr = devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
      if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const ca = Math.cos(a), sa = Math.sin(a);
      const rot = (x: number, y: number) => [x * ca - y * sa, x * sa + y * ca];
      const proj0 = (x: number, y: number, z: number) => { const [u, v] = rot(x, y); return [(u - v) * 0.866, (u + v) * 0.5 - z]; };

      // fit everything (prims + beams) into the canvas
      const pts: number[][] = [];
      const extent = (x: number, y: number, z: number) => pts.push(proj0(x, y, z));
      for (const p of prims) {
        if (p.type === "box") for (const x of p.x) for (const y of p.y) for (const z of p.z) extent(x, y, z);
        else if (p.type === "wheel" || p.type === "lens") { const [x, y, z] = p.c; extent(x - p.r, y - p.r, z - p.r); extent(x + p.r, y + p.r, z + p.r); }
        else { const [x, y, z] = p.c; extent(x - p.r, y - p.r, z); extent(x + p.r, y + p.r, z + p.h); }
      }
      for (const b of beams) { const r = (b.angle * Math.PI) / 180; extent(b.from[0] + Math.cos(r) * b.length, b.from[1] + Math.sin(r) * b.length, b.from[2]); }
      if (!pts.length) return;
      const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
      const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
      const s = Math.min((w - 30) / Math.max(maxX - minX, 0.1), (h - 30) / Math.max(maxY - minY, 0.1));
      const ox = w / 2 - ((minX + maxX) / 2) * s, oy = h / 2 - ((minY + maxY) / 2) * s;
      const P = (x: number, y: number, z: number) => { const [px, py] = proj0(x, y, z); return [ox + px * s, oy + py * s]; };
      const poly = (q: number[][], fill: string, stroke?: string) => {
        ctx.beginPath(); q.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath();
        ctx.fillStyle = fill; ctx.fill();
        if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 1; ctx.stroke(); }
      };
      const depth = (x: number, y: number) => { const [u, v] = rot(x, y); return u + v; };

      // ground shadow
      ctx.fillStyle = "rgba(0,0,0,.18)";
      const [gx, gy] = P(0, 0, 0);
      ctx.beginPath(); ctx.ellipse(gx, gy, (maxX - minX) * s * 0.42, (maxX - minX) * s * 0.12, 0, 0, Math.PI * 2); ctx.fill();

      const items = prims.map((p) => {
        const c = p.type === "box" ? [(p.x[0] + p.x[1]) / 2, (p.y[0] + p.y[1]) / 2] : [p.c[0], p.c[1]];
        return { p, d: depth(c[0], c[1]) + (p.type === "lens" ? 0.02 : 0) };   // lenses sit on their housing's face
      }).sort((m, n) => m.d - n.d);
      const line = css("--line-2") || "rgba(0,0,0,.2)";

      const drawSlicePlane = () => {
        if (sliceZ == null) return;
        const R = 2.8;
        poly([P(-R, -R, sliceZ), P(R, -R, sliceZ), P(R, R, sliceZ), P(-R, R, sliceZ)], "rgba(229,138,82,.10)", "rgba(229,138,82,.35)");
        for (const [x0, x1, y0, y1] of slices) poly([P(x0, y0, sliceZ), P(x1, y0, sliceZ), P(x1, y1, sliceZ), P(x0, y1, sliceZ)], "rgba(229,138,82,.95)");
      };
      let planeDrawn = false;
      for (const { p } of items) {
        const base = p.color ?? color;
        const below = p.type === "box" ? p.z[0] < (sliceZ ?? -1) : true;
        if (!planeDrawn && !below) { drawSlicePlane(); planeDrawn = true; }
        if (p.type === "box") {
          // box with its own yaw: draw only the side faces turned toward the viewer, then the top
          const cx = (p.x[0] + p.x[1]) / 2, cy = (p.y[0] + p.y[1]) / 2, hx = (p.x[1] - p.x[0]) / 2, hy = (p.y[1] - p.y[0]) / 2;
          const [z0, z1] = p.z, yw = ((p.yaw ?? 0) * Math.PI) / 180, cw = Math.cos(yw), sw = Math.sin(yw);
          const W = (lx: number, ly: number, z: number) => P(cx + lx * cw - ly * sw, cy + lx * sw + ly * cw, z);
          const sides: [number, number, [number, number][]][] = [
            [1, 0, [[hx, -hy], [hx, hy]]], [-1, 0, [[-hx, hy], [-hx, -hy]]], [0, 1, [[hx, hy], [-hx, hy]]], [0, -1, [[-hx, -hy], [hx, -hy]]]];
          for (const [nx, ny, [[ax, ay], [bx, by]]] of sides) {
            const wx = nx * cw - ny * sw, wy = nx * sw + ny * cw;
            const facing = depth(wx, wy);
            if (facing <= 0) continue;
            poly([W(ax, ay, z0), W(bx, by, z0), W(bx, by, z1), W(ax, ay, z1)], shade(base, 0.68 + 0.25 * Math.min(1, facing / 1.4)), line);
          }
          poly([W(-hx, -hy, z1), W(hx, -hy, z1), W(hx, hy, z1), W(-hx, hy, z1)], shade(base, 1.12), line);
        } else if (p.type === "lens") {
          // round optic on a housing face (TF-Luna emitter / receiver): dark glass with a highlight
          const yw = (p.yaw * Math.PI) / 180, nx = Math.cos(yw), ny = Math.sin(yw);
          if (depth(nx, ny) <= 0) continue;
          const [x, y, z] = p.c, n = 24;
          const pts = Array.from({ length: n }, (_, i) => { const t = (i / n) * Math.PI * 2;
            return P(x - Math.sin(yw) * p.r * Math.cos(t), y + Math.cos(yw) * p.r * Math.cos(t), z + p.r * Math.sin(t)); });
          poly(pts, p.color ?? "#1b2330", "rgba(255,255,255,.35)");
          const [hx, hy] = P(x - Math.sin(yw) * p.r * -0.35, y + Math.cos(yw) * p.r * -0.35, z + p.r * 0.35);
          ctx.fillStyle = "rgba(255,255,255,.55)"; ctx.beginPath(); ctx.arc(hx, hy, Math.max(1.2, p.r * s * 0.22), 0, Math.PI * 2); ctx.fill();
        } else if (p.type === "wheel") {
          const [x, y, z] = p.c, n = 28;
          for (const yy of [y - p.w / 2, y + p.w / 2]) {
            const ring = Array.from({ length: n }, (_, i) => { const t = (i / n) * Math.PI * 2; return P(x + p.r * Math.cos(t), yy, z + p.r * Math.sin(t)); });
            if (p.rim >= p.r * 0.9) poly(ring, shade("#3a3632", 1), line);
            else {
              ctx.beginPath(); ring.forEach(([u, v], i) => (i ? ctx.lineTo(u, v) : ctx.moveTo(u, v))); ctx.closePath();
              ctx.strokeStyle = "#3a3632"; ctx.lineWidth = Math.max(1.5, p.rim * s); ctx.stroke();
            }
          }
        } else {
          const [x, y, z] = p.c, n = 28;
          const ring = (zz: number) => Array.from({ length: n }, (_, i) => { const t = (i / n) * Math.PI * 2; return P(x + p.r * Math.cos(t), y + p.r * Math.sin(t), zz); });
          const bot = ring(z), top = ring(z + p.h);
          for (let i = 0; i < n; i++) poly([bot[i], bot[(i + 1) % n], top[(i + 1) % n], top[i]], shade(base, 0.7 + 0.3 * Math.abs(Math.cos((i / n) * Math.PI * 2 + a))));
          poly(top, shade(base, 1.15), line);
        }
      }
      if (!planeDrawn) drawSlicePlane();

      // beams (live range-finder readings, or a dashed outline when there is no reading)
      ctx.font = `500 10.5px ${css("--mono")}`;
      for (const b of beams) {
        const r = (b.angle * Math.PI) / 180;
        const [x0, y0] = P(...b.from), [x1, y1] = P(b.from[0] + Math.cos(r) * b.length, b.from[1] + Math.sin(r) * b.length, b.from[2]);
        const g = ctx.createLinearGradient(x0, y0, x1, y1);
        g.addColorStop(0, b.color); g.addColorStop(1, b.live ? b.color : "transparent");
        ctx.strokeStyle = g; ctx.lineWidth = b.live ? 2.5 : 1.5; ctx.setLineDash(b.live ? [] : [4, 5]);
        ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke(); ctx.setLineDash([]);
        if (b.live) { ctx.fillStyle = b.color; ctx.beginPath(); ctx.arc(x1, y1, 4, 0, Math.PI * 2); ctx.fill(); }
        ctx.fillStyle = css("--muted"); ctx.fillText(b.label, x1 + 6, y1 - 6);
      }
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [spin, yaw]);

  return <canvas ref={ref} style={{ width: "100%", height, display: "block" }} aria-hidden />;
}

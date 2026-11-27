import { useCallback, useEffect, useState } from "react";
import { api, CAT_COLOR, Frame, POS_NAME, RF } from "./api";
import Iso3D, { Beam, Prim } from "./Iso3D";
import { BEAM_COLOR } from "./ScanCanvas";

type Models = Record<string, { prims: Prim[] }>;
const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const TOP: Record<string, number> = { car: 1.45, motorcycle: 1.45, bicycle: 1.5, pedestrian: 1.45, unknown: 1.0 };

/** Move a model (built around its own origin, x = forward) to world position (X, Y) facing `yawDeg`. */
function place(prims: Prim[], X: number, Y: number, yawDeg: number, color: string): Prim[] {
  const t = (yawDeg * Math.PI) / 180, c = Math.cos(t), s = Math.sin(t);
  const at = (x: number, y: number): [number, number] => [X + x * c - y * s, Y + x * s + y * c];
  return prims.map((p): Prim => {
    if (p.type === "box") {
      const [cx, cy] = at((p.x[0] + p.x[1]) / 2, (p.y[0] + p.y[1]) / 2), hx = (p.x[1] - p.x[0]) / 2, hy = (p.y[1] - p.y[0]) / 2;
      return { ...p, x: [cx - hx, cx + hx], y: [cy - hy, cy + hy], yaw: yawDeg + (p.yaw ?? 0), color };
    }
    if (p.type === "wheel") { const [x, y] = at(p.c[0], p.c[1]); return { ...p, c: [x, y, p.c[2]], yaw: yawDeg }; }
    return p;
  });
}

/** Live Scan's 3D digital-twin view: tracked objects shown as the twin's 3D model of their detected type,
 * at their tracked position and heading, over the real scan points. Shapes are illustrations: the LiDAR
 * measures a flat slice, so only position, footprint, heading and type come from the sensor. */
export default function TwinView({ frameRef, rfRef, rangeMax, showEgo = true }: {
  frameRef: React.MutableRefObject<Frame | null>; rfRef?: React.MutableRefObject<RF | null>; rangeMax: number; showEgo?: boolean }) {
  const [models, setModels] = useState<Models | null>(null);
  useEffect(() => { api<Models>("/api/twin/preview?height_m=0.6").then(setModels).catch(() => setModels({})); }, []);

  const getScene = useCallback(() => {
    const f = frameRef.current;
    const veh = rfRef?.current?.vehicle;
    const front = veh?.front_deg ?? f?.front_deg ?? 90, vx = veh?.x ?? 0, vy = veh?.y ?? 0;
    const ego = showEgo ? models?.motorcycle?.prims : undefined;
    const prims: Prim[] = ego
      ? [...place(ego, vx, vy, front, "#E9DFCF"),                                          // your motorcycle, nose = vehicle front
         { type: "cyl", c: [0, 0, 0.86], r: 0.1, h: 0.08, color: "#C96F4A" }]               // the LiDAR at its mount
      : [{ type: "cyl", c: [0, 0, 0], r: 0.14, h: 0.32, color: "#2a2826" },                // the LiDAR on its mount
         { type: "cyl", c: [0, 0, 0.32], r: 0.11, h: 0.08, color: "#C96F4A" }];
    for (const t of f?.tracks ?? []) {
      const color = CAT_COLOR[t.category] ?? "#9A938B";
      const along = t.box[2] - t.box[0] >= t.box[3] - t.box[1] ? 0 : 90;            // footprint's long side
      const yaw = t.heading ?? along;
      const m = models?.[t.category]?.prims;
      if (m) prims.push(...place(m, t.x, t.y, yaw, color));
      else prims.push({ type: "box", x: [t.box[0], t.box[2]], y: [t.box[1], t.box[3]], z: [0, 1.0], color });
    }
    // the three TF-Luna beams, from their mounting points (one distance each)
    const beams: Beam[] = (rfRef?.current?.sensors ?? []).map((s) => ({
      from: [s.x, s.y, 0.6], angle: s.angle, length: s.m ?? 1.5, live: s.m != null, color: BEAM_COLOR[s.pos],
      label: s.m != null ? `${POS_NAME[s.pos]} ${s.m.toFixed(2)} m` : "" }));
    return { prims, beams };
  }, [frameRef, rfRef, models, showEgo]);

  const ground = useCallback((ctx: CanvasRenderingContext2D, P: (x: number, y: number, z: number) => number[]) => {
    // distance rings on the road
    ctx.lineWidth = 1;
    for (let r = 2; r <= rangeMax; r += 2) {
      ctx.strokeStyle = r % 4 === 0 ? css("--grid-2") : css("--grid");
      ctx.beginPath();
      for (let i = 0; i <= 72; i++) { const a = (i / 72) * Math.PI * 2; const [x, y] = P(r * Math.cos(a), r * Math.sin(a), 0); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }
      ctx.stroke();
      const [lx, ly] = P(r, 0, 0);
      ctx.fillStyle = css("--faint"); ctx.font = `10px ${css("--mono")}`; ctx.fillText(`${r} m`, lx + 4, ly + 12);
    }
    // the real scan points, lying on the ground
    const f = frameRef.current;
    if (!f) return;
    for (const [x, y, k] of f.points) {
      if (Math.hypot(x, y) > rangeMax) continue;
      const [px, py] = P(x, y, 0);
      ctx.fillStyle = k === 0 ? css("--faint") : k === 1 ? css("--accent-hi") : css("--text");
      ctx.fillRect(px - 1, py - 1, 2, 2);
    }
  }, [frameRef, rangeMax]);

  const overlay = useCallback((ctx: CanvasRenderingContext2D, P: (x: number, y: number, z: number) => number[]) => {
    if (showEgo) {   // FRONT / BACK / LEFT / RIGHT on the ground around your motorcycle
      const veh = rfRef?.current?.vehicle;
      const front = veh?.front_deg ?? frameRef.current?.front_deg ?? 90, cx = veh?.x ?? 0, cy = veh?.y ?? 0;
      ctx.font = `700 11px ${css("--mono")}`; ctx.textAlign = "center";
      for (const [txt, off, dist] of [["FRONT", 0, 1.9], ["BACK", 180, 2.1], ["LEFT", 90, 1.2], ["RIGHT", -90, 1.2]] as const) {
        const a = ((front + off) * Math.PI) / 180;
        const [x, y] = P(cx + Math.cos(a) * dist, cy + Math.sin(a) * dist, 0);
        const tw = ctx.measureText(txt).width;
        ctx.fillStyle = "rgba(20,19,18,.72)"; ctx.beginPath(); ctx.roundRect(x - tw / 2 - 6, y - 8, tw + 12, 17, 8); ctx.fill();
        ctx.fillStyle = txt === "FRONT" ? css("--accent-hi") : "#E9DFCF";
        ctx.fillText(txt, x, y + 4);
      }
      ctx.textAlign = "left";
    }
    ctx.font = `600 11px ${css("--font")}`;
    const placed: [number, number, number][] = [];                       // [x, y, width] of labels already drawn
    for (const t of frameRef.current?.tracks ?? []) {
      let [x, y] = P(t.x, t.y, (TOP[t.category] ?? 1) + 0.4);
      const label = `#${t.id} ${t.category} · ${t.range.toFixed(1)} m`;
      const w = ctx.measureText(label).width;
      // stack labels that would overlap (an object split into pieces gives several nearby tracks)
      while (placed.some(([px, py, pw]) => Math.abs(px - x) < (pw + w) / 2 + 8 && Math.abs(py - y) < 20)) y -= 22;
      placed.push([x, y, w]);
      ctx.fillStyle = css("--glass"); ctx.beginPath(); ctx.roundRect(x - w / 2 - 6, y - 14, w + 12, 18, 6); ctx.fill();
      ctx.fillStyle = css("--text"); ctx.fillText(label, x - w / 2, y);
    }
  }, [frameRef, rfRef, showEgo]);

  return <Iso3D getScene={getScene} ground={ground} overlay={overlay} extent={rangeMax * 1.2} spin={false} yaw={-40} interactive height="100%" />;
}

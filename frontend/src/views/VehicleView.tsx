import { useEffect, useRef } from "react";
import { Frame, fmt, LEVEL_COLOR, LEVEL_NAME, lidarState, POS_NAME, RF, TFLSensor, useLive } from "../api";
import { BEAM_COLOR, drawEgoBike } from "../ScanCanvas";
import { Header } from "./Views";

const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const SIDE_OF: Record<string, "left" | "right" | "rear"> = { L: "left", R: "right", B: "rear" };

/** Top-down vehicle view, front up. Vehicle frame: x forward, y left. TF-Lunas are ONE distance each (a ray from
 * the mount); LiDAR points are the real scan, moved into the vehicle frame. Contours show the configured
 * clearance thresholds around the body. */
function VehicleCanvas({ frameRef, rfRef }: { frameRef: React.MutableRefObject<Frame | null>; rfRef: React.MutableRefObject<RF | null> }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current!, ctx = cv.getContext("2d")!;
    let raf = 0;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      const dpr = devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
      if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const rf = rfRef.current, f = frameRef.current;
      if (!rf) return;
      const v = rf.vehicle, th = rf.thresholds, cx = w / 2, cy = h / 2;
      const reach = Math.max(4, th.caution + Math.max(v.length, v.width));
      const scale = Math.min(w, h) / (2 * reach);
      // vehicle frame -> screen (front up, left = screen left)
      const S = (vx: number, vy: number) => [cx - vy * scale, cy - vx * scale];
      // plot frame -> vehicle frame
      const a = (-v.front_deg * Math.PI) / 180, ca = Math.cos(a), sa = Math.sin(a);
      const toV = (px: number, py: number) => { const x = px - v.x, y = py - v.y; return [x * ca - y * sa, x * sa + y * ca]; };

      // clearance contours around the body: caution, warning, danger
      const contour = (d: number, col: string) => {
        const [x0, y0] = S(v.length / 2 + d, v.width / 2 + d), [x1, y1] = S(-v.length / 2 - d, -v.width / 2 - d);
        ctx.beginPath(); ctx.roundRect(x0, y0, x1 - x0, y1 - y0, d * scale);
        ctx.strokeStyle = col; ctx.lineWidth = 1.2; ctx.setLineDash([5, 5]); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = col; ctx.font = `10px ${css("--mono")}`; ctx.fillText(`${d.toFixed(1)} m`, x1 + 4, y0 + 12);
      };
      contour(th.caution, "rgba(229,180,82,.55)"); contour(th.warning, "rgba(229,138,82,.7)"); contour(th.danger, "rgba(217,100,74,.85)");

      // LiDAR points (only from a fresh frame; foreground = new/moving things)
      if (f && Date.now() / 1000 - ((f as Frame & { t_wall?: number }).t_wall ?? 0) < 1.5) {
        for (const [px, py, k] of f.points) {
          const [vx, vy] = toV(px, py);
          const [sx, sy] = S(vx, vy);
          if (sx < 0 || sy < 0 || sx > w || sy > h) continue;
          ctx.fillStyle = k === 0 ? css("--faint") : css("--accent-hi");
          ctx.fillRect(sx - 1.2, sy - 1.2, 2.4, 2.4);
        }
      }
      // your vehicle, front up
      drawEgoBike(ctx, cx, cy, scale, 90, { text: css("--text"), muted: css("--muted"), faint: css("--faint"), accent: css("--accent-hi") }, true);
      // the YDLIDAR at its mount
      const [lx, ly] = S(...(toV(0, 0) as [number, number]));
      ctx.fillStyle = css("--accent"); ctx.beginPath(); ctx.arc(lx, ly, 5, 0, Math.PI * 2); ctx.fill();
      // the three TF-Lunas: a ray from the mount along its facing direction, length = the measured distance
      for (const s of rf.sensors) {
        const [mx, my] = s.mount_vehicle, t = (s.yaw_vehicle * Math.PI) / 180;
        const [bx, by] = S(mx, my);
        const len = s.m ?? 8, lvl = rf.monitor?.sides[SIDE_OF[s.pos]]?.level ?? 0;
        const [ex, ey] = S(mx + Math.cos(t) * len, my + Math.sin(t) * len);
        ctx.strokeStyle = s.m != null ? LEVEL_COLOR[lvl] : BEAM_COLOR[s.pos];
        ctx.globalAlpha = s.m != null ? 1 : 0.3; ctx.lineWidth = s.m != null ? 3 : 1.2; ctx.setLineDash(s.m != null ? [] : [4, 6]);
        ctx.beginPath(); ctx.moveTo(bx, by); ctx.lineTo(ex, ey); ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
        ctx.fillStyle = BEAM_COLOR[s.pos]; ctx.fillRect(bx - 4, by - 4, 8, 8);
        if (s.m != null) {
          ctx.fillStyle = LEVEL_COLOR[lvl]; ctx.beginPath(); ctx.arc(ex, ey, 6, 0, Math.PI * 2); ctx.fill();
          ctx.font = `700 12px ${css("--mono")}`; ctx.fillStyle = css("--text");
          ctx.fillText(`${s.m.toFixed(2)} m`, ex + 9, ey + 4);
        }
      }
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [frameRef, rfRef]);
  return <canvas ref={ref} style={{ width: "100%", height: "100%", display: "block" }} role="img"
    aria-label="Top-down view of the vehicle with the three TF-Luna distances and LiDAR obstacles" />;
}

const ago = (t: number | null) => (t == null ? "never" : `${Math.max(0, Date.now() / 1000 - t).toFixed(1)} s ago`);

function TFLCard({ s, level }: { s: TFLSensor; level: number }) {
  const ok = s.status === "OK" || s.status === "SIMULATED";
  return (
    <div className="card">
      <div className="spread"><h3><span className="beam-chip" style={{ background: BEAM_COLOR[s.pos] }} />{s.name}</h3>
        <span className="chip"><span className={`dot ${ok && s.fresh ? (s.status === "SIMULATED" ? "warn" : "ok") : s.fresh ? "warn" : ""}`} />{s.status}</span></div>
      <div className="stat"><div className="v" style={{ color: s.m != null ? LEVEL_COLOR[level] : undefined }}>{s.m != null ? `${s.m.toFixed(2)} m` : "—"}</div>
        <div className="s">{s.m != null ? LEVEL_NAME[level] : s.fresh ? "no valid distance" : "no fresh data"}</div></div>
      <div className="kv" style={{ fontSize: 12, marginTop: 10 }}>
        <span>Position</span><span>{POS_NAME[s.pos]} · sensor {s.id}</span>
        <span>Last good reading</span><span>{ago(s.last_ok_t)}</span>
        <span>Data age</span><span>{s.age_s == null ? "—" : `${s.age_s.toFixed(2)} s`}</span>
        <span>Signal strength</span><span>{s.amp ?? "—"}</span>
        <span>Frames ok / bad</span><span>{s.frames_ok} / {s.frames_bad}</span>
        <span>Source</span><span>{s.source === "simulation" ? "SIMULATION" : "Pico W"}</span>
      </div>
    </div>
  );
}

export default function VehicleView() {
  const { frameRef, rfRef, rf, status: s, frame } = useLive();
  const mon = rf?.monitor, st = lidarState(s), sim = s?.source === "simulation";
  const pico = s?.rangefinders;
  return (
    <>
      <Header title="Vehicle" sub="Left, right and rear TF-Luna distances plus the 360° LiDAR, around your vehicle.">
        {sim && <span className="stamp">simulation</span>}
        {rf && !rf.vehicle.calibrated && <span className="chip accent" title="Set the real mounting positions in Settings">Geometry uncalibrated</span>}
      </Header>
      <div className="grid" style={{ gridTemplateColumns: "minmax(340px, 1.3fr) 1fr", marginBottom: 14 }}>
        <div className="card" style={{ height: 560, padding: 0, overflow: "hidden" }}><div className="scanwrap">
          {rf ? <VehicleCanvas frameRef={frameRef} rfRef={rfRef} /> : <div className="empty"><b>Starting up…</b></div>}
        </div></div>
        <div className="grid" style={{ gap: 10, alignContent: "start" }}>
          {(["front", "left", "right", "rear"] as const).map((side) => {
            const v = mon?.sides[side];
            return (
              <div key={side} className="card" style={{ padding: "12px 16px", borderColor: v?.level ? LEVEL_COLOR[v.level] : undefined }}>
                <div className="spread">
                  <b style={{ textTransform: "uppercase", letterSpacing: ".1em", fontSize: 12 }}>{side}</b>
                  <span className="chip" style={{ color: LEVEL_COLOR[v?.level ?? 0], borderColor: LEVEL_COLOR[v?.level ?? 0] }}>{v?.label ?? "—"}</span>
                </div>
                <div className="mono" style={{ fontSize: 20, fontWeight: 700, marginTop: 4 }}>{v?.nearest_m != null ? `${v.nearest_m.toFixed(2)} m` : "clear"}</div>
                <div className="faint" style={{ fontSize: 11.5 }}>{v?.source ? `nearest from ${v.source}` : side === "front" ? "YDLIDAR only (no TF-Luna at the front)" : "nothing within range"}</div>
              </div>);
          })}
          <div className="faint" style={{ fontSize: 11.5 }}>
            Distances are clearance from the vehicle body. Thresholds: DANGER ≤ {rf?.thresholds.danger} m, WARNING ≤ {rf?.thresholds.warning} m,
            CAUTION ≤ {rf?.thresholds.caution} m. These are adjustable research values (Settings), not certified safety limits. A TF-Luna measures distance only;
            it cannot tell what the object is.
          </div>
        </div>
      </div>
      <div className="grid g4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))", marginBottom: 14 }}>
        <div className="card">
          <div className="spread"><h3>YDLIDAR T-mini Plus</h3><span className="chip"><span className={`dot ${st.cls}`} />{st.label}</span></div>
          <div className="stat"><div className="v">{s?.lidar.freq_measured ? `${s.lidar.freq_measured.toFixed(1)} Hz` : "—"}</div><div className="s">360° scan rate</div></div>
          <div className="kv" style={{ fontSize: 12, marginTop: 10 }}>
            <span>Port</span><span>{s?.lidar.port ?? "—"}</span>
            <span>Last scan</span><span>{ago(s?.lidar.last_scan_t ?? null)}</span>
            <span>Packets ok / rejected</span><span>{s?.lidar.packets_ok ?? 0} / {s?.lidar.packets_bad ?? 0}</span>
            <span>Objects in view</span><span>{frame?.tracks.length ?? 0}</span>
          </div>
        </div>
        {(rf?.sensors ?? []).map((x) => <TFLCard key={x.pos} s={x} level={mon?.sides[SIDE_OF[x.pos]]?.level ?? 0} />)}
      </div>
      <div className="card">
        <h3>System status</h3>
        <div className="kv" style={{ fontSize: 12.5 }}>
          <span>Pico W</span><span>{sim ? "not used (simulation)" : `${pico?.state ?? "—"}${pico?.port ? ` on ${pico.port}` : ""}${pico?.firmware ? ` · firmware ${pico.firmware}` : ""}`}</span>
          <span>TF-Luna Left / Right / Rear</span><span>{(rf?.sensors ?? []).map((x) => x.status).join(" / ") || "—"}</span>
          <span>YDLIDAR</span><span>{s?.lidar.state ?? "—"}</span>
          <span>Data acquisition</span><span>{s?.processing.ms != null ? `${s.processing.ms.toFixed(1)} ms per scan, ${s.processing.dropped_scans} dropped` : "—"}</span>
          <span>Communication errors</span><span>{`${pico?.lines_bad ?? 0} Pico lines rejected, ${s?.lidar.packets_bad ?? 0} LiDAR packets rejected`}{pico?.errors?.length ? ` · last: ${pico.errors[pico.errors.length - 1]}` : ""}</span>
          <span>Last update</span><span>{pico?.last_t ? fmt.time(pico.last_t) : s?.lidar.last_scan_t ? fmt.time(s.lidar.last_scan_t) : "—"}</span>
        </div>
      </div>
    </>
  );
}

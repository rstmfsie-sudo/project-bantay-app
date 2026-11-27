import { useCallback, useEffect, useState } from "react";
import { api, CAT_COLOR, CATEGORIES, Config, DATA_LABEL, fmt, lidarState, METHOD_LABEL, MLMeta, POS_NAME, RF, setSource, Track, TFLSensor, useLive, useToast } from "../api";
import type { ViewProps } from "../App";
import Iso3D, { Beam, Prim, roundedRect } from "../Iso3D";
import { tfLuna } from "../sensors3d";
import ScanCanvas, { BEAM_COLOR, DEFAULT_LAYERS } from "../ScanCanvas";

/** One friendly italic line under each page title. */
const TAGLINE: Record<string, string> = {
  Overview: "everything the sensor sees, at a glance",
  "Object Detection": "what's out there, and how sure we are",
  Tracking: "following each object from scan to scan",
  Analytics: "patterns hiding in the traffic",
  "Data History": "every detection, timestamped and searchable",
  "Dataset Manager": "real examples you've taught it",
  "Digital Twin": "a pretend road, so it can practise",
  "Machine Learning": "turning your examples into a classifier",
  Vehicle: "what's around your ride, on every side",
  Hardware: "the sensors, and how they're connected",
  Settings: "make it fit your roadside",
};
export function Header({ title, sub, children }: { title: string; sub?: string; children?: React.ReactNode }) {
  return (
    <div className="page-h">
      <div>
        <h1>{title}</h1>
        {TAGLINE[title] && <span className="serif">{TAGLINE[title]}</span>}
        {sub && <p>{sub}</p>}
      </div>
      <div className="row">{children}</div>
    </div>
  );
}
export function Stat({ k, v, s, dot, tone }: { k: string; v: React.ReactNode; s?: React.ReactNode; dot?: string; tone?: string }) {
  return <div className={`card stat ${tone ? `c-${tone}` : ""}`}><div className="k row" style={{ gap: 7 }}>{dot !== undefined && <span className={`dot ${dot}`} />}{k}</div><div className="v">{v}</div>{s && <div className="s">{s}</div>}</div>;
}
function useFetch<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const reload = useCallback(() => {
    if (!path) return;
    api<T>(path).then((d) => { setData(d); setErr(null); }).catch((e) => setErr(e.message));
  }, [path]);
  useEffect(() => { reload(); }, [reload, ...deps]); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, err, reload };
}
const Cat = ({ c }: { c: string }) => <span><span className="cat" style={{ background: CAT_COLOR[c] ?? "#888" }} />{c}</span>;
const healthBits = ["Sensor", "Encoder", "Wireless power", "Laser feedback (PD)", "Laser drive (LD)", "Data"];

/** 3D model of the real sensors (metres): three upright TF-Luna modules around the YDLIDAR T-mini Plus.
 * Each TF-Luna faces its beam; beams show LIVE readings. The T-mini Plus is a simplified look-alike. */
export function SensorRig({ rf, height = 240 }: { rf: RF | null; height?: number }) {
  const sensorsIn: Pick<TFLSensor, "pos" | "yaw_vehicle" | "m">[] = rf?.sensors ??
    [{ pos: "L", yaw_vehicle: 90, m: null }, { pos: "R", yaw_vehicle: -90, m: null }, { pos: "B", yaw_vehicle: 180, m: null }];
  const PLATE_Z = 0.02, TOP = PLATE_Z + 0.005, R = 0.062;   // TF-Lunas around the plate, facing their mounting direction
  const prims: Prim[] = [
    { type: "box", x: [-0.01, 0.01], y: [-0.01, 0.01], z: [0, PLATE_Z], color: "#4a4540", order: -10 },            // mount stub
    { type: "prism", c: [0, 0, PLATE_Z + 0.0025], yaw: 0, depth: 0.15, outline: roundedRect(0.15, 0.005, 0.002), color: "#3a3531", order: -9 }, // plate
    { type: "box", x: [-0.026, 0.026], y: [-0.022, 0.022], z: [TOP, TOP + 0.024], color: "#232120" },             // T-mini Plus body
    { type: "cyl", c: [0, 0, TOP + 0.024], r: 0.022, h: 0.016, color: "#151414" },                                 // rotating head
    { type: "lens", c: [0.022, 0, TOP + 0.032], r: 0.005, yaw: 0, color: "#1e2733" },                              // head window
  ];
  const beams: Beam[] = [];
  for (const b of sensorsIn) {
    const rel = b.yaw_vehicle, t = (rel * Math.PI) / 180;    // vehicle frame: 0 = forward, 90 = left
    const zc = TOP + 0.006 + 0.0175;                         // housing centre height (ears clear the plate)
    const px = R * Math.cos(t), py = R * Math.sin(t);
    prims.push(...tfLuna([px, py, zc], rel, BEAM_COLOR[b.pos]));
    const fx = px + 0.0068 * Math.cos(t), fy = py + 0.0068 * Math.sin(t);
    const vis = b.m != null ? 0.04 + 0.012 * Math.min(b.m, 4) : 0.035;   // drawn short so the sensors stay readable
    beams.push({ from: [fx, fy, zc], angle: rel, length: vis, live: b.m != null,
      label: `${POS_NAME[b.pos]}${b.m != null ? ` · ${b.m.toFixed(2)} m` : ""}`, color: BEAM_COLOR[b.pos] });
  }
  return <Iso3D prims={prims} beams={beams} height={height} yaw={-55} />;
}

/** Large rotating TF-Luna, to show what the range finders look like. */
export function TfLunaCloseup({ height = 220 }: { height?: number }) {
  return <Iso3D prims={tfLuna([0, 0, 0.02], 0)} height={height} yaw={-35} />;
}

// ============================================================== OVERVIEW
export function Overview({ go }: ViewProps) {
  const { frameRef, rfRef, frame, status: s, rf, alert } = useLive();
  const st = lidarState(s);
  const beam = (pos: "L" | "R" | "B") => rf?.sensors.find((b) => b.pos === pos)?.m;
  return (
    <>
      <Header title="Overview" />
      <div className="grid g4" style={{ marginBottom: 14 }}>
        <Stat k="360° LiDAR" dot={st.cls} v={st.label} s={s?.lidar.port ? `on ${s.lidar.port}` : "not plugged in"} />
        <Stat k="Turning at" tone="orange" v={s?.lidar.freq_measured ? `${s.lidar.freq_measured.toFixed(1)} Hz` : "—"} s="full sweeps per second" />
        <Stat k="Objects seen" tone="blue" v={s?.counts.objects ?? 0} s="since the app opened" />
        <Stat k="Recognised" tone="green" v={s?.counts.classified ?? 0} s={s?.ml.active && s.ml.available ? "by the Random Forest" : s?.nn?.active && s.nn.available ? "by the neural network" : "by shape rules"} />
        <Stat k="Not sure yet" tone="purple" v={s?.counts.unknown ?? 0} s="better safe than wrong" />
        <Stat k="Right now" dot={alert.S === 2 ? "bad" : alert.S === 1 ? "warn" : "ok"}
          v={alert.S === 2 ? "Very close" : alert.S === 1 ? "Something near" : "All clear"} s={alert.reasons[0] ?? "nothing inside the warning zones"} />
      </div>
      <div className="grid" style={{ gridTemplateColumns: "minmax(320px, 1.25fr) 1fr", marginBottom: 14 }}>
        <div className="card" style={{ padding: 0, overflow: "hidden", height: 400 }}>
          <div className="scanwrap" onDoubleClick={() => go("live")}>
            <ScanCanvas frameRef={frameRef} rfRef={rfRef} rangeMax={8} layers={{ ...DEFAULT_LAYERS, labels: false }} compact />
            <div className="row" style={{ position: "absolute", left: 16, top: 16 }}>
              <span className="stamp">{frame?.source === "simulation" ? "simulation" : frame?.source === "replay" ? "replay" : st.cls === "ok" ? "● live" : "no signal"}</span>
              <span className="stamp">2D · single plane</span>
            </div>
            {!frame && <div className="empty" style={{ position: "absolute", inset: 0 }}><b>Waiting for the LiDAR</b><span className="faint">Nothing here is made up: no sensor, no dots.</span>
              <button className="btn sm primary" onClick={() => setSource("simulation")}>No LiDAR with you? Try the simulation</button></div>}
            <button className="btn sm overlay" style={{ right: 12, bottom: 12, padding: "5px 10px" }} onClick={() => go("live")}>Open Live Scan</button>
          </div>
        </div>
        <div className="card">
          <h3>Your sensors</h3>
          <SensorRig rf={rf} height={210} />
          <div className="grid" style={{ gridTemplateColumns: "repeat(3, 1fr)", gap: 8, marginTop: 6 }}>
            {(["L", "R", "B"] as const).map((b) => (
              <div key={b} className="mono" style={{ fontSize: 12 }}>
                <span className="beam-chip" style={{ background: BEAM_COLOR[b] }} />{POS_NAME[b]}
                <div style={{ fontSize: 18, fontWeight: 700, fontFamily: "var(--font)" }}>{beam(b) != null ? `${beam(b)!.toFixed(2)} m` : "—"}</div>
              </div>
            ))}
          </div>
          <div className="faint" style={{ fontSize: 11.5, marginTop: 8 }}>
            {s?.rangefinders.state === "connected" ? "Three TF-Luna range finders live on the Pico W." : "TF-Luna range finders: " + (s?.rangefinders.message ?? "…")}
          </div>
        </div>
      </div>
      <div className="card">
        <h3>Who's around</h3>
        <TrackTable tracks={frame?.tracks ?? []} compact />
      </div>
    </>
  );
}

function TrackTable({ tracks, compact }: { tracks: Track[]; compact?: boolean }) {
  if (!tracks.length) return <div className="empty"><b>No objects in view</b><span>Confirmed tracks appear here.</span></div>;
  return (
    <div style={{ overflow: "auto", maxHeight: compact ? 310 : undefined }}>
      <table className="t">
        <thead><tr><th>ID</th><th>Category</th><th>Distance</th><th>Bearing</th>{!compact && <><th>Position (x, y)</th><th>Duration</th><th>Direction</th><th>Speed (est.)</th><th>TTC</th></>}<th>Risk</th></tr></thead>
        <tbody>{tracks.map((t) => (
          <tr key={t.id}>
            <td className="mono">#{t.id}</td><td><Cat c={t.category} /></td>
            <td className="mono">{fmt.m(t.range)}</td><td className="mono">{fmt.deg(t.bearing)}</td>
            {!compact && <>
              <td className="mono">{t.x.toFixed(2)}, {t.y.toFixed(2)}</td><td className="mono">{fmt.s(t.duration)}</td>
              <td className="mono">{t.heading == null ? "stationary / n.a." : fmt.deg(t.heading)}</td>
              <td className="mono">{t.speed == null ? "too few scans" : `${t.speed.toFixed(1)} m/s`}</td>
              <td className="mono">{fmt.s(t.ttc)}</td>
            </>}
            <td><span className={`chip ${t.risk !== "CLEAR" ? "accent" : ""}`}>{t.risk}</span></td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

// ============================================================== DETECTION
export function Detection() {
  const { frame, status: s } = useLive();
  const tracks = frame?.tracks ?? [];
  return (
    <>
      <Header title="Object Detection" sub="Everything the LiDAR picked out of this sweep, and what it thinks each one is." />
      <div className="note" style={{ marginBottom: 14 }}>
        <b>Method:</b> {s?.ml.active && s.ml.available ? `ML (${s.ml.meta?.validated ? "validated" : "experimental"}) · model ${s.ml.meta?.version}. Confidence is the model's uncalibrated class probability.` :
          "Rule-based geometry. Rules give no confidence score. Two-wheelers stay Unknown because a single 2D plane cannot reliably tell motorcycles from bicycles."}
      </div>
      <div className="grid g4" style={{ marginBottom: 14 }}>
        <Stat k="Clusters (this scan)" v={frame?.clusters.length ?? "—"} />
        <Stat k="Confirmed tracks" v={tracks.length} />
        {CATEGORIES.map((c) => <Stat key={c} k={c[0].toUpperCase() + c.slice(1)} v={tracks.filter((t) => t.category === c).length} />)}
      </div>
      <div className="card">
        <h3>Current classifications</h3>
        {!tracks.length ? <div className="empty"><b>No objects detected</b><span>{s?.background.ready ? "Nothing new or moving in the scene." : "The background is warming up (about 10 s after start)."}</span></div> : (
          <table className="t">
            <thead><tr><th>Track</th><th>Category</th><th>Method</th><th>Confidence</th><th>Points</th><th>Extent (max)</th><th>Width</th><th>Linearity</th><th>Reason</th></tr></thead>
            <tbody>{tracks.map((t) => (
              <tr key={t.id}><td className="mono">#{t.id}</td><td><Cat c={t.category} /></td><td>{METHOD_LABEL[t.method]}</td>
                <td className="mono">{t.confidence == null ? "n/a" : t.confidence.toFixed(2)}</td><td className="mono">{t.features.n_points}</td>
                <td className="mono">{fmt.m(t.max_length)}</td><td className="mono">{fmt.m(t.features.width_m)}</td>
                <td className="mono">{t.features.linearity.toFixed(2)}</td><td className="muted">{t.note}</td></tr>
            ))}</tbody>
          </table>
        )}
      </div>
    </>
  );
}

// ============================================================== TRACKING
export function Tracking() {
  const { frame } = useLive();
  return (
    <>
      <Header title="Tracking" sub="Each object keeps the same number while it moves, so you can follow it." />
      <div className="note plain" style={{ marginBottom: 14 }}>
        Speed and direction appear only after enough consecutive scans. They are estimates from the cluster centroid,
        which moves as the visible side of an object changes, so treat them as indicative, not measured.
        TTC is toward the sensor, using the proposal's thresholds (WARNING under 1.5 s or inside 2 m, CAUTION under 3 s or inside 4 m).
      </div>
      <div className="card"><TrackTable tracks={frame?.tracks ?? []} /></div>
    </>
  );
}

// ============================================================== ANALYTICS
type Stats = { by_category: { category: string; method: string; objects: number }[]; series: { t: number; category: string; objects: number }[];
  distance_hist: { m: number; count: number }[]; bucket_s: number };
export function Analytics() {
  const [hours, setHours] = useState(24);
  const { data, err } = useFetch<Stats>(`/api/stats?hours=${hours}`, [hours]);
  const byCat = CATEGORIES.map((c) => ({ c, n: (data?.by_category ?? []).filter((r) => r.category === c).reduce((a, r) => a + r.objects, 0) }));
  const max = Math.max(1, ...byCat.map((x) => x.n));
  const buckets = [...new Set((data?.series ?? []).map((r) => r.t))].sort();
  const maxB = Math.max(1, ...buckets.map((b) => (data?.series ?? []).filter((r) => r.t === b).reduce((a, r) => a + r.objects, 0)));
  const maxD = Math.max(1, ...(data?.distance_hist ?? []).map((d) => d.count));
  return (
    <>
      <Header title="Analytics" sub="How many of each, and when. Every object is counted once.">
        <div className="seg">{[1, 24, 168, 720].map((h) => <button key={h} className={hours === h ? "on" : ""} onClick={() => setHours(h)}>{h === 1 ? "1 h" : h === 24 ? "24 h" : h === 168 ? "7 d" : "30 d"}</button>)}</div>
      </Header>
      {err && <div className="note">{err}</div>}
      <div className="grid g2">
        <div className="card">
          <h3>Objects by category</h3>
          <div className="bars">{byCat.map(({ c, n }) => (
            <div className="bar" key={c}><Cat c={c} /><div className="track"><div className="fill" style={{ width: `${(n / max) * 100}%`, background: CAT_COLOR[c] }} /></div><span className="mono" style={{ textAlign: "right" }}>{n}</span></div>
          ))}</div>
          <div className="faint" style={{ fontSize: 11.5, marginTop: 12 }}>Methods: {[...new Set((data?.by_category ?? []).map((r) => METHOD_LABEL[r.method] ?? r.method))].join(", ") || "none yet"}</div>
        </div>
        <div className="card">
          <h3>Detections by range (log rows)</h3>
          {!data?.distance_hist.length ? <div className="empty"><b>No data in this period</b></div> : (
            <svg viewBox="0 0 300 120" style={{ width: "100%", height: 160 }} role="img" aria-label="Distance histogram">
              {data.distance_hist.map((d) => {
                const h = (d.count / maxD) * 100, x = (d.m / 12) * 290;
                return <g key={d.m}><rect x={x} y={105 - h} width={22} height={h} rx={3} fill="var(--accent)" /><text x={x + 11} y={117} fontSize={8} textAnchor="middle" fill="var(--faint)">{d.m}</text></g>;
              })}
            </svg>
          )}
        </div>
        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <h3>Objects over time ({data ? `${data.bucket_s / 60} min buckets` : ""})</h3>
          {!buckets.length ? <div className="empty"><b>No detections logged in this period</b><span>History builds up while the sensor or a replay is running.</span></div> : (
            <svg viewBox={`0 0 ${Math.max(buckets.length * 14, 300)} 130`} style={{ width: "100%", height: 180 }} preserveAspectRatio="none" role="img" aria-label="Objects over time, stacked by category">
              {buckets.map((b, i) => {
                let y = 120;
                return <g key={b}>{CATEGORIES.map((c) => {
                  const n = data!.series.find((r) => r.t === b && r.category === c)?.objects ?? 0;
                  const h = (n / maxB) * 110; y -= h;
                  return n ? <rect key={c} x={i * 14 + 2} y={y} width={10} height={h} fill={CAT_COLOR[c]}><title>{`${fmt.time(b)} · ${c}: ${n}`}</title></rect> : null;
                })}</g>;
              })}
            </svg>
          )}
        </div>
      </div>
    </>
  );
}

// ============================================================== HISTORY
type DetRow = { id: number; ts: number; track_id: number; category: string; method: string; confidence: number | null; model_version: string;
  distance: number; angle: number; speed: number | null; length: number; n_points: number; risk: string };
export function History() {
  const [f, setF] = useState({ hours: "24", category: "", method: "", track: "" });
  const [page, setPage] = useState(0);
  const since = f.hours ? Date.now() / 1000 - Number(f.hours) * 3600 : "";
  const q = new URLSearchParams({ ...(since ? { since: String(since) } : {}), ...(f.category ? { category: f.category } : {}),
    ...(f.method ? { method: f.method } : {}), ...(f.track ? { track_id: f.track } : {}), limit: "100", offset: String(page * 100) });
  const { data, err } = useFetch<{ total: number; rows: DetRow[] }>(`/api/detections?${q}`, [q.toString()]);
  const set = (k: string, v: string) => { setPage(0); setF((x) => ({ ...x, [k]: v })); };
  const csvQ = new URLSearchParams([...q].filter(([k]) => !["limit", "offset", "track_id"].includes(k)));
  return (
    <>
      <Header title="Data History" sub="A logbook of everything it saw while the real sensor was running.">
        <a className="btn" href={`/api/detections.csv?${csvQ}`}>Export CSV</a>
      </Header>
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="row">
          <label className="field">Period<select value={f.hours} onChange={(e) => set("hours", e.target.value)}>
            <option value="1">Last hour</option><option value="24">Last 24 h</option><option value="168">Last 7 days</option><option value="">All</option></select></label>
          <label className="field">Category<select value={f.category} onChange={(e) => set("category", e.target.value)}>
            <option value="">All</option>{CATEGORIES.map((c) => <option key={c}>{c}</option>)}</select></label>
          <label className="field">Method<select value={f.method} onChange={(e) => set("method", e.target.value)}>
            <option value="">All</option>{Object.entries(METHOD_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label className="field">Track ID<input type="text" inputMode="numeric" value={f.track} onChange={(e) => set("track", e.target.value.replace(/\D/g, ""))} placeholder="any" style={{ width: 90 }} /></label>
        </div>
      </div>
      <div className="card">
        {err && <div className="note">{err}</div>}
        {!data?.rows.length ? <div className="empty"><b>No records</b><span>Try a wider period or clear the filters.</span></div> : (
          <>
            <table className="t">
              <thead><tr><th>Time</th><th>Track</th><th>Category</th><th>Method</th><th>Conf.</th><th>Model</th><th>Distance</th><th>Angle</th><th>Speed (est.)</th><th>Risk</th></tr></thead>
              <tbody>{data.rows.map((r) => (
                <tr key={r.id}><td className="mono">{fmt.time(r.ts)}</td><td className="mono">#{r.track_id}</td><td><Cat c={r.category} /></td>
                  <td>{METHOD_LABEL[r.method] ?? r.method}</td><td className="mono">{r.confidence == null ? "n/a" : r.confidence.toFixed(2)}</td>
                  <td className="mono faint">{r.model_version}</td><td className="mono">{fmt.m(r.distance)}</td><td className="mono">{fmt.deg(r.angle)}</td>
                  <td className="mono">{r.speed == null ? "—" : `${r.speed.toFixed(1)} m/s`}</td><td>{r.risk}</td></tr>
              ))}</tbody>
            </table>
            <div className="spread" style={{ marginTop: 12 }}>
              <span className="muted">{data.total} records</span>
              <div className="row"><button className="btn sm" disabled={!page} onClick={() => setPage(page - 1)}>Previous</button>
                <span className="mono muted">{page + 1} / {Math.max(1, Math.ceil(data.total / 100))}</span>
                <button className="btn sm" disabled={(page + 1) * 100 >= data.total} onClick={() => setPage(page + 1)}>Next</button></div>
            </div>
          </>
        )}
      </div>
    </>
  );
}

// ============================================================== DATASET
type Sample = { id: number; ts: number; label: string; track_id: number; features: Record<string, number>; points: [number, number][]; source: string };
function Shape({ pts }: { pts: [number, number][] }) {
  if (!pts.length) return null;
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), y0 = Math.min(...ys), s = Math.max(Math.max(...xs) - x0, Math.max(...ys) - y0, 0.2);
  return <svg viewBox="-4 -4 108 108" width={54} height={54} style={{ background: "var(--surface-2)", borderRadius: 8 }}>
    {pts.map(([x, y], i) => <circle key={i} cx={((x - x0) / s) * 100} cy={100 - ((y - y0) / s) * 100} r={2.4} fill="var(--accent-hi)" />)}
  </svg>;
}
export function DatasetManager({ go }: ViewProps) {
  const toast = useToast();
  const [filter, setFilter] = useState("");
  const { data, reload } = useFetch<Sample[]>(`/api/dataset?points=true${filter ? `&label=${filter}` : ""}`, [filter]);
  const all = useFetch<Sample[]>("/api/dataset", [data]);
  const { status } = useLive();
  const counts = CATEGORIES.map((c) => ({ c, n: (all.data ?? []).filter((s) => s.label === c).length, o: status?.dataset?.[c]?.objects ?? 0 }));
  const relabel = async (id: number, label: string) => { await api(`/api/dataset/${id}`, { method: "PATCH", json: { label } }); reload(); };
  const del = async (id: number) => { await api(`/api/dataset/${id}`, { method: "DELETE" }); toast({ title: "Sample deleted" }); reload(); };
  return (
    <>
      <Header title="Dataset Manager" sub="The objects you've labeled yourself. These are what the models learn from.">
        <a className="btn" href="/api/dataset/export.csv">Export features (CSV)</a>
        <a className="btn" href="/api/dataset/export.json">Export full (JSON)</a>
      </Header>
      <div className="note plain" style={{ marginBottom: 14 }}>
        <b>How to collect:</b> open <a href="#" onClick={(e) => { e.preventDefault(); go("live"); }} style={{ color: "var(--accent-text)" }}>Live Scan</a>,
        click an object while you can see it with your own eyes (or on a synchronized video), then choose its true category.
        Each sample stores the cluster's points and its geometric and temporal features. Collect varied angles, distances and speeds.
      </div>
      <div style={{ marginBottom: 14 }}><SyntheticDatasetCard /></div>
      <h3 className="muted" style={{ font: "500 11.5px var(--mono)", letterSpacing: ".12em", textTransform: "uppercase", margin: "0 0 10px" }}>Real partition · labeled by you</h3>
      <div className="grid g4" style={{ marginBottom: 14 }}>{counts.map(({ c, n, o }) => <Stat key={c} k={c} v={n} s={c === "unknown" ? "not used for training" : `${o} objects · ${o < 3 || n < 5 ? "need 3+ objects, 5+ samples" : "ready"}`} />)}</div>
      <div className="card">
        <div className="spread" style={{ marginBottom: 10 }}>
          <h3 style={{ margin: 0 }}>Samples</h3>
          <div className="seg">{["", ...CATEGORIES].map((c) => <button key={c} className={filter === c ? "on" : ""} onClick={() => setFilter(c)}>{c || "all"}</button>)}</div>
        </div>
        {!data?.length ? <div className="empty"><b>No labeled samples yet</b><span>A trained model needs at least 2 categories with 5+ samples each, and far more for meaningful results.</span></div> : (
          <table className="t">
            <thead><tr><th>Shape</th><th>ID</th><th>Captured</th><th>Label</th><th>Points</th><th>Extent</th><th>Range</th><th>Source</th><th /></tr></thead>
            <tbody>{data.map((s) => (
              <tr key={s.id}><td><Shape pts={s.points} /></td><td className="mono">{s.id}</td><td className="mono">{fmt.time(s.ts)}</td>
                <td><select value={s.label} onChange={(e) => relabel(s.id, e.target.value)}>{CATEGORIES.map((c) => <option key={c}>{c}</option>)}</select></td>
                <td className="mono">{s.features.n_points}</td><td className="mono">{fmt.m(s.features.track_max_length_m)}</td>
                <td className="mono">{fmt.m(s.features.range_m)}</td><td>{s.source}</td>
                <td><button className="btn sm ghost" onClick={() => del(s.id)}>Delete</button></td></tr>
            ))}</tbody>
          </table>
        )}
      </div>
    </>
  );
}

// ============================================================== MACHINE LEARNING
type ModelKind = "rf" | "nn";
const MODEL_INFO: Record<ModelKind, { name: string; blurb: string; minObjects: number }> = {
  rf: { name: "Random Forest", minObjects: 3,
    blurb: "Learns from 8 measurements of the object itself (length, width, shape, gaps, laser coverage, reflectivity, speed). Distance and location are deliberately left out, so a model trained at one road works at another. Works with small datasets. Start here." },
  nn: { name: "Neural network", minObjects: 10,
    blurb: "PointNet-style network that learns from the object's raw 2D points plus the same 8 object measurements. Needs much more data (10+ objects per category minimum, hundreds to beat the forest)." },
};

function ModelPanel({ kind, meta, installed = true }: { kind: ModelKind; meta: MLMeta | null; installed?: boolean }) {
  const toast = useToast();
  const info = MODEL_INFO[kind];
  const setValidated = async (v: boolean) => {
    await api("/api/ml/validated", { method: "POST", json: { value: v, model: kind } });
    toast({ title: v ? `${info.name} marked validated` : "Validation mark removed" });
  };
  return (
    <div className="card">
      <div className="spread" style={{ alignItems: "flex-start" }}>
        <div>
          <h3>{info.name}</h3>
          <div className="muted" style={{ fontSize: 12.5, maxWidth: 520, marginTop: -4 }}>{info.blurb}</div>
        </div>
        {meta ? <span className="chip accent">trained on {DATA_LABEL[meta.training_data ?? "real"]}</span>
          : !installed && <span className="chip">PyTorch not installed</span>}
      </div>
      {!meta ? (
        <div className="empty"><b>Not trained yet</b>
          <span>Train it with the card above: on the digital twin now, on your real labels later ({info.minObjects}+ objects per category).</span></div>
      ) : (
        <>
          <div className="grid g4" style={{ margin: "14px 0" }}>
            <Stat k={meta.training_data === "twin" ? "Accuracy on simulated objects" : "Grouped CV accuracy"} v={`${(meta.cv_accuracy * 100).toFixed(1)}%`}
              s={`${meta.cv_folds}-fold, objects held out`} />
            {meta.training_data !== "twin" && meta.training_data !== undefined && meta.training_data !== "real" && <Stat k="On real objects" tone="green"
              v={meta.real_cv ? `${(meta.real_cv.accuracy * 100).toFixed(1)}%` : "—"} s={meta.real_cv ? `${meta.real_cv.samples} real samples, held out` : "no real samples yet"} />}
            <Stat k="Objects" v={Object.values(meta.objects ?? {}).reduce((a, b) => a + b, 0)} s={Object.entries(meta.objects ?? {}).map(([k, v]) => `${k} ${v}`).join(" · ")} />
            <Stat k="Status" v={meta.validated ? "Validated" : "Experimental"} s={meta.version} />
          </div>
          <div className="grid g2">
            <div>
              <table className="t"><thead><tr><th>true \ predicted</th>{meta.classes.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                <tbody>{meta.confusion_matrix.map((row, i) => <tr key={i}><td><Cat c={meta.classes[i]} /></td>{row.map((v, j) => (
                  <td key={j} className="mono" style={{ background: i === j && v ? "color-mix(in srgb, var(--accent) 22%, transparent)" : undefined }}>{v}</td>))}</tr>)}</tbody></table>
            </div>
            {meta.per_class && <div>
              <table className="t"><thead><tr><th>Category</th><th>Precision</th><th>Recall</th><th>F1</th></tr></thead>
                <tbody>{Object.entries(meta.per_class).map(([c, m]) => (
                  <tr key={c}><td><Cat c={c} /></td><td className="mono">{m.precision.toFixed(2)}</td><td className="mono">{m.recall.toFixed(2)}</td>
                    <td className="mono">{m.f1.toFixed(2)}</td></tr>))}</tbody></table>
            </div>}
          </div>
          {meta.feature_importance && <details style={{ marginTop: 12 }}><summary className="muted" style={{ cursor: "pointer" }}>Feature importance</summary>
            <div className="bars" style={{ marginTop: 10 }}>{Object.entries(meta.feature_importance).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
              <div className="bar" key={k} style={{ gridTemplateColumns: "150px 1fr 48px" }}><span className="mono" style={{ fontSize: 11.5 }}>{k}</span>
                <div className="track"><div className="fill" style={{ width: `${v * 100 / Math.max(...Object.values(meta.feature_importance!))}%`, background: "var(--accent)" }} /></div>
                <span className="mono" style={{ textAlign: "right" }}>{v.toFixed(3)}</span></div>))}</div></details>}
          <label className="row" style={{ marginTop: 14, cursor: "pointer", fontSize: 12.5 }}>
            <input type="checkbox" checked={meta.validated} onChange={(e) => setValidated(e.target.checked)} />
            <span>Mark as field-validated. Do this only after testing on new data from a different day or site.</span></label>
          <div className="faint" style={{ fontSize: 11.5, marginTop: 8 }}>{meta.note} Trained {fmt.time(meta.trained_at)}. Small datasets give unstable estimates.</div>
        </>
      )}
    </div>
  );
}

/** Train the Random Forest and/or the neural network on the chosen data, as one background job. */
export function TrainCard({ height, perClass = 60, data: initial }: { height: number; perClass?: number; data?: "twin" | "real" | "both" }) {
  const { status: s } = useLive();
  const toast = useToast();
  const realObjects = Object.entries(s?.dataset ?? {}).filter(([k]) => k !== "unknown").reduce((a, [, v]) => a + v.objects, 0);
  const [data, setData] = useState<"twin" | "real" | "both">(initial ?? (realObjects >= 6 ? "both" : "twin"));
  const [models, setModels] = useState<string[]>(["rf", "nn"]);
  const job = s?.ml_job;
  const start = async () => {
    try {
      await api("/api/ml/train", { method: "POST", json: { data, models, height_m: height, objects_per_class: perClass, activate: true } });
      toast({ title: "Training started", body: "It runs in the background; the neural network takes a few minutes." });
    } catch (e) { toast({ title: "Couldn't start training", body: (e as Error).message, kind: "warn" }); }
  };
  const toggle = (m: string) => setModels((ms) => (ms.includes(m) ? ms.filter((x) => x !== m) : [...ms, m]));
  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <div className="spread" style={{ alignItems: "flex-end", gap: 16 }}>
        <div className="row" style={{ gap: 22 }}>
          <label className="field">Learn from
            <div className="seg">
              <button className={data === "twin" ? "on" : ""} onClick={() => setData("twin")}>Digital twin</button>
              <button className={data === "real" ? "on" : ""} disabled={!realObjects} onClick={() => setData("real")}>My real labels</button>
              <button className={data === "both" ? "on" : ""} onClick={() => setData("both")}>Both</button>
            </div></label>
          <label className="field">Models
            <div className="row">
              <label className="row" style={{ cursor: "pointer" }}><input type="checkbox" checked={models.includes("rf")} onChange={() => toggle("rf")} /> Random Forest</label>
              <label className="row" style={{ cursor: "pointer" }}><input type="checkbox" checked={models.includes("nn")} disabled={!s?.nn?.installed} onChange={() => toggle("nn")} /> Neural network</label>
            </div></label>
        </div>
        <button className="btn primary" disabled={job?.running || !models.length} onClick={start}>
          {job?.running ? `Training ${Math.round(job.progress * 100)}%` : "Train and switch to the better model"}</button>
      </div>
      {job?.running && <div className="bar" style={{ gridTemplateColumns: "1fr", marginTop: 12 }}>
        <div className="track"><div className="fill" style={{ width: `${job.progress * 100}%`, background: "var(--accent)" }} /></div></div>}
      <div className="faint" style={{ fontSize: 11.5, marginTop: 8 }}>
        {job && job.message !== "idle" ? job.message : `${realObjects} real labeled objects so far.`}
        {" "}Simulated data teaches the shapes; only real labels tell you how it does on your road.
      </div>
    </div>
  );
}

export function SyntheticDatasetCard() {
  const { status: s } = useLive();
  const d = s?.twin_dataset;
  return (
    <div className="card">
      <div className="spread"><h3>Synthetic partition · digital twin</h3>{d && <a className="btn sm" href="/api/dataset/synthetic.csv">Export CSV</a>}</div>
      {!d ? <div className="muted" style={{ fontSize: 12.5 }}>Not generated yet. Training on the digital twin creates it.</div> : <>
        <table className="t"><thead><tr><th>Category</th><th>Objects</th><th>Samples</th></tr></thead>
          <tbody>{d.classes.map((c) => <tr key={c}><td><Cat c={c} /></td><td className="mono">{d.objects[c]}</td><td className="mono">{d.samples[c]}</td></tr>)}</tbody></table>
        <div className="faint" style={{ fontSize: 11.5, marginTop: 8 }}>{d.version} · {d.generator} · scan plane {d.mount_height_m.toFixed(2)} m · created {fmt.time(d.created)}.
          {" "}{d.note} "Unknown" is not a learned class: it's what a model answers when its confidence is below the threshold.</div>
      </>}
    </div>
  );
}

export function MachineLearning() {
  const { status: s } = useLive();
  const [cfg, setCfg] = useState<Config | null>(null);
  useEffect(() => { api<Config>("/api/config").then(setCfg); }, []);
  const setClassifier = async (v: string) => setCfg(await api<Config>("/api/config", { method: "PATCH", json: { classifier: v } }));
  const rf = s?.ml.meta ?? null, nn = s?.nn?.meta ?? null;
  const better = rf && nn ? (nn.cv_accuracy > rf.cv_accuracy ? "Neural network" : "Random Forest") : null;
  return (
    <>
      <Header title="Machine Learning" sub="Models that learn from the objects you label, and are always tested on objects they haven't seen." />
      <TrainCard height={Number(cfg?.mount_height_m ?? 0.6)} />
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="spread">
          <h3 style={{ margin: 0 }}>Classifier in use</h3>
          <div className="seg">
            <button className={cfg?.classifier === "rules" ? "on" : ""} onClick={() => setClassifier("rules")}>Rule-based</button>
            <button className={cfg?.classifier === "ml" ? "on" : ""} disabled={!s?.ml.available} onClick={() => setClassifier("ml")}>Random Forest</button>
            <button className={cfg?.classifier === "nn" ? "on" : ""} disabled={!s?.nn?.available} onClick={() => setClassifier("nn")}>Neural network</button>
          </div>
        </div>
        <div className="muted" style={{ fontSize: 12.5, marginTop: 10 }}>
          Results below {cfg ? Number(cfg.ml_min_confidence).toFixed(2) : "—"} confidence are reported as <i>unknown</i> (change in Settings).
          {better && <> On your current data, the <b>{better}</b> scores higher in grouped cross-validation.</>}
        </div>
      </div>
      <div className="grid" style={{ gap: 14 }}>
        <ModelPanel kind="rf" meta={rf} />
        <ModelPanel kind="nn" meta={nn} installed={s?.nn?.installed ?? false} />
      </div>
      <div className="note plain" style={{ marginTop: 14 }}>
        <b>Getting good classification:</b> mount the sensor level and keep its height fixed (a model only knows the height it was trained at).
        Label many <i>different</i> objects per category, across distances, directions and speeds. Use <b>Follow</b> so one click captures
        an object from several angles, or label calmly from a <b>recording</b> in Live Scan. Compare both models here and use the better one.
        <br /><br />
        <b>Limits:</b> a single horizontal plane sees one slice of each object. Motorcycles and bicycles can produce near-identical
        slices, and objects seen end-on look small. Expect confusion between those classes and report it honestly.
      </div>
    </>
  );
}

// ============================================================== HARDWARE
export function Hardware() {
  const { status: s, rf } = useLive();
  const toast = useToast();
  const [cfg, setCfg] = useState<Config | null>(null);
  const [hz, setHz] = useState(6);
  const recs = useFetch<{ name: string; bytes: number; modified: number }[]>("/api/recordings", [s?.recording]);
  useEffect(() => { api<Config>("/api/config").then(setCfg); }, []);
  const patch = async (p: Config) => { try { setCfg(await api<Config>("/api/config", { method: "PATCH", json: p })); } catch (e) { toast({ title: "Setting rejected", body: (e as Error).message, kind: "bad" }); } };
  const l = s?.lidar;
  const st = lidarState(s);
  return (
    <>
      <Header title="Hardware" sub="The 360° LiDAR plugs in over USB. The three single-beam LiDARs connect through the Raspberry Pi Pico W." />
      <div className="grid g2">
        <div className="card">
          <div className="spread"><h3>YDLIDAR T-mini Plus</h3><span className="chip"><span className={`dot ${st.cls}`} /> {st.label}</span></div>
          <div className="faint" style={{ marginBottom: 12, fontSize: 12 }}>{l?.message}</div>
          <div className="kv">
            <span>Port</span><span>{l?.port ?? "—"}</span>
            <span>Model code</span><span>{l?.model != null ? `${l.model}${l.model === 151 ? " (T-mini Plus)" : ""}` : "—"}</span>
            <span>Firmware / hardware</span><span>{l?.firmware ?? "—"} / {l?.hardware ?? "—"}</span>
            <span>Serial</span><span style={{ fontSize: 11 }}>{l?.serial ?? "—"}</span>
            <span>Health</span><span>{l?.health == null ? "—" : l.health === 0 ? "all modules normal" : healthBits.filter((_, i) => l.health! & (1 << i)).join(", ") + " abnormal"}</span>
            <span>Frequency reported / measured</span><span>{l?.freq_reported ?? "—"} / {l?.freq_measured?.toFixed(2) ?? "—"} Hz</span>
            <span>Packets ok / rejected</span><span>{l?.packets_ok ?? 0} / {l?.packets_bad ?? 0}</span>
            <span>Revolutions</span><span>{l?.scans ?? 0}</span>
          </div>
          <div className="row" style={{ marginTop: 14 }}>
            <label className="field">Serial port<select value={String(cfg?.port ?? "auto")} onChange={(e) => patch({ port: e.target.value })}>
              <option value="auto">Auto-detect</option>{s?.ports.map((p) => <option key={p.device} value={p.device}>{p.device} · {p.description}</option>)}</select></label>
            <label className="field">Baud<input type="number" value={Number(cfg?.baud ?? 230400)} onChange={(e) => patch({ baud: Number(e.target.value) })} style={{ width: 100 }} /></label>
            <button className="btn" style={{ alignSelf: "end" }} onClick={() => api("/api/lidar/reconnect", { method: "POST" })}>Reconnect</button>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <label className="field">Scan frequency (6–12 Hz)<input type="range" min={6} max={12} step={0.5} value={hz} onChange={(e) => setHz(Number(e.target.value))} /></label>
            <span className="mono" style={{ alignSelf: "end" }}>{hz} Hz</span>
            <button className="btn" style={{ alignSelf: "end" }} disabled={l?.state !== "scanning"} onClick={async () => {
              const r = await api<{ note: string }>("/api/lidar/frequency", { method: "POST", json: { hz } }); toast({ title: `Setting ${hz} Hz`, body: r.note }); }}>Apply</button>
          </div>
        </div>
        <div className="card">
          <div className="spread"><h3>Pico W · three TF-Luna range finders</h3>
            <span className="chip"><span className={`dot ${s?.rangefinders.state === "connected" ? "ok" : s?.pico.detected ? "warn" : ""}`} />
              {s?.rangefinders.state === "connected" ? `Live on ${s.rangefinders.port} · ${s.rangefinders.rate_hz.toFixed(0)} Hz` : s?.pico.detected ? `Pico on ${s.pico.port}` : "Pico not plugged in"}</span></div>
          <div className="grid" style={{ gridTemplateColumns: "1.5fr 1fr", gap: 8, alignItems: "center" }}>
            <SensorRig rf={rf} height={210} />
            <div><TfLunaCloseup height={190} /><div className="faint" style={{ fontSize: 11, textAlign: "center" }}>TF-Luna · 35 × 21 × 13.5 mm</div></div>
          </div>
          <table className="t" style={{ margin: "4px 0 10px" }}>
            <thead><tr><th>Sensor</th><th>Pico pin</th><th>Status</th><th>Distance</th><th>Signal</th><th>Frames ok / bad</th></tr></thead>
            <tbody>{(rf?.sensors ?? []).map((b) => (
              <tr key={b.pos}><td><span className="beam-chip" style={{ background: BEAM_COLOR[b.pos] }} />{b.name}</td>
                <td className="mono">{b.pos === "L" ? "GP16/17 · UART0" : b.pos === "R" ? "GP8/9 · UART1" : "GP4/5 · I2C0"}</td>
                <td><span className="chip"><span className={`dot ${b.status === "OK" ? "ok" : b.status === "SIMULATED" ? "warn" : b.fresh ? "warn" : ""}`} />{b.status}</span></td>
                <td className="mono">{b.m != null ? `${b.m.toFixed(2)} m` : "—"}</td>
                <td className="mono">{b.amp ?? "—"}</td>
                <td className="mono">{b.frames_ok} / {b.frames_bad}</td></tr>))}</tbody>
          </table>
          <div className="muted" style={{ fontSize: 12 }}>{s?.rangefinders.message}{s?.rangefinders.firmware ? ` · firmware ${s.rangefinders.firmware}` : ""}
            {s ? ` · ${s.rangefinders.lines_ok} lines ok, ${s.rangefinders.lines_bad} rejected` : ""}</div>
          <details style={{ marginTop: 10 }}><summary className="muted" style={{ cursor: "pointer" }}>Wiring (confirmed on your Pico)</summary>
            <div className="kv" style={{ fontSize: 12, marginTop: 8 }}>
              <span>Sensor 1 · Left (UART)</span><span>GP16 TX / GP17 RX · UART0</span>
              <span>Sensor 2 · Right (UART)</span><span>GP8 TX / GP9 RX · UART1</span>
              <span>Sensor 3 · Rear (I2C)</span><span>GP4 SDA / GP5 SCL · I2C0 · 0x10</span>
              <span>TF-Luna power</span><span>5 V + GND</span>
              <span>Warning LEDs</span><span>GP18 / GP19 / GP20</span><span>Buzzer</span><span>GP15 (quiet PWM)</span>
            </div>
            <div className="faint" style={{ fontSize: 11.5, marginTop: 8 }}>Each sensor has its own peripheral (UART0, UART1, I2C0), so there's no conflict.
              Confirmed from the program that was on your Pico (backed up in firmware/pico_backup) and a live test where all three answered.</div>
          </details>
          <h3 style={{ marginTop: 16 }}>Detected serial ports</h3>
          {!s?.ports.length ? <div className="muted">No serial ports found.</div> : (
            <table className="t"><thead><tr><th>Port</th><th>Description</th><th>VID:PID</th></tr></thead>
              <tbody>{s.ports.map((p) => <tr key={p.device}><td className="mono">{p.device}</td><td>{p.description}</td>
                <td className="mono">{p.vid != null ? `${p.vid.toString(16).padStart(4, "0")}:${p.pid?.toString(16).padStart(4, "0")}` : "—"}</td></tr>)}</tbody></table>
          )}
        </div>
        <div className="card">
          <h3>Background model</h3>
          <p className="muted" style={{ marginTop: 0 }}>Updates itself continuously, so you never need an empty road. Anything that moves is detected. Anything that stays still for about {s?.background.absorb_s ?? 30} s (a parked car, a new sign) blends into the scene. Walls and poles are ignored. The machine learning never sees this: it only classifies the detected objects.</p>
          <div className="row">
            <span className="chip"><span className={`dot ${s?.background.ready ? "ok" : "warn"}`} />
              {!s?.background.enabled ? "Off" : s.background.ready ? "Adaptive · up to date" : `Warming up ${Math.round(s.background.progress * 100)}%`}</span>
            <button className="btn" onClick={() => api("/api/background/reset", { method: "POST" })}>Re-learn now</button>
            <span className="muted" style={{ fontSize: 12 }}>Use after moving the sensor.</span>
          </div>
        </div>
        <div className="card">
          <h3>Recording & replay</h3>
          <p className="muted" style={{ marginTop: 0 }}>Recording saves every original revolution (angles, distances, intensity, flags) to <span className="mono">data/recordings</span>. Replay re-runs a recording through the pipeline and is labelled REPLAY everywhere.</p>
          <div className="row" style={{ marginBottom: 10 }}>
            <label className="row" style={{ cursor: "pointer" }}><input type="checkbox" checked={!!cfg?.record} onChange={(e) => patch({ record: e.target.checked })} /> Record raw scans</label>
            <div className="seg"><button className={cfg?.source === "serial" ? "on" : ""} onClick={() => patch({ source: "serial" })}>Live sensor</button>
              <button className={cfg?.source === "replay" ? "on" : ""} disabled={!recs.data?.length} onClick={() => patch({ source: "replay", replay_file: String(cfg?.replay_file || recs.data?.[0]?.name || "") })}>Replay</button></div>
          </div>
          {cfg?.source === "replay" && <div className="row" style={{ marginBottom: 10 }}>
            <select value={String(cfg.replay_file)} onChange={(e) => patch({ replay_file: e.target.value })}>{recs.data?.map((r) => <option key={r.name}>{r.name}</option>)}</select>
            <button className="btn sm" onClick={() => api("/api/replay/play", { method: "POST" })}>Play again</button>
            <span className="mono muted">{s?.replay.message} {s?.replay.total ? `${s.replay.pos}/${s.replay.total}` : ""}</span>
          </div>}
          <div className="muted" style={{ fontSize: 12 }}>{recs.data?.length ?? 0} recordings{recs.data?.[0] ? `, latest ${recs.data[0].name} (${(recs.data[0].bytes / 1e6).toFixed(1)} MB)` : ""}</div>
        </div>
      </div>
    </>
  );
}

// ============================================================== SETTINGS
const FIELDS: { group: string; items: [string, string, number?][] }[] = [
  { group: "Geometry", items: [["angle_offset_deg", "Mounting yaw offset (°)", 1], ["mirror", "Sensor angles clockwise → plot counter-clockwise"]] },
  { group: "Filtering", items: [["range_min_m", "Minimum range (m)", 0.01], ["range_max_m", "Maximum range (m)", 0.5], ["drop_flagged", "Drop interference-flagged points (flag 2/3)"],
    ["min_intensity", "Minimum intensity (0–255)", 1], ["background_enabled", "Use background subtraction"], ["background_margin_m", "Background margin (m)", 0.05], ["background_absorb_s", "Still object becomes background after (s)", 5]] },
  { group: "Clustering (DBSCAN)", items: [["dbscan_eps_m", "Neighbourhood ε (m)", 0.01], ["dbscan_min_samples", "Min samples", 1], ["min_cluster_points", "Min points per object", 1]] },
  { group: "Alerts", items: [["alert_sound", "Soft chime when something is close"], ["alert_volume", "Chime volume (0–1)", 0.05],
    ["alert_light", "Gentle glow around the window"]] },
  { group: "TF-Luna sensors (Pico W)", items: [["rf_enabled", "Use the three TF-Luna sensors"]] },
  { group: "Vehicle geometry (uncalibrated until measured)", items: [["geometry_calibrated", "I measured these on the real vehicle"],
    ["vehicle_length_m", "Vehicle length (m)", 0.05], ["vehicle_width_m", "Vehicle width (m)", 0.05],
    ["lidar_mount_x_m", "YDLIDAR position forward (m)", 0.05], ["lidar_mount_y_m", "YDLIDAR position left (m)", 0.05],
    ["tfl_L_x_m", "Left TF-Luna forward (m)", 0.05], ["tfl_L_y_m", "Left TF-Luna left (m)", 0.05], ["tfl_L_yaw_deg", "Left TF-Luna facing (°)", 1],
    ["tfl_R_x_m", "Right TF-Luna forward (m)", 0.05], ["tfl_R_y_m", "Right TF-Luna left (m)", 0.05], ["tfl_R_yaw_deg", "Right TF-Luna facing (°)", 1],
    ["tfl_B_x_m", "Rear TF-Luna forward (m)", 0.05], ["tfl_B_y_m", "Rear TF-Luna left (m)", 0.05], ["tfl_B_yaw_deg", "Rear TF-Luna facing (°)", 1]] },
  { group: "Obstacle thresholds (research values, not certified)", items: [["prox_danger_m", "DANGER within (m)", 0.05],
    ["prox_warning_m", "WARNING within (m)", 0.05], ["prox_caution_m", "CAUTION within (m)", 0.05]] },
  { group: "Tracking", items: [["track_max_speed_mps", "Fastest object to expect (m/s)", 1], ["track_gate_m", "Association gate (m)", 0.1], ["track_max_missed", "Drop after missed scans", 1], ["track_confirm_hits", "Confirm after hits", 1], ["speed_min_frames", "Show speed after updates", 1]] },
  { group: "Risk zones (from proposal)", items: [["zone_warning_m", "Warning zone (m)", 0.1], ["zone_caution_m", "Caution zone (m)", 0.1], ["ttc_warning_s", "TTC warning (s)", 0.1], ["ttc_caution_s", "TTC caution (s)", 0.1]] },
  { group: "Classification & storage", items: [["ml_min_confidence", "ML minimum confidence", 0.05], ["log_interval_s", "Log interval per track (s)", 0.5], ["retention_days", "Keep history (days)", 1], ["open_browser", "Open browser on start"],
    ["exit_when_unplugged", "Close the app when the lidar is unplugged"], ["unplug_grace_s", "…after it is gone for (s)", 5]] },
];
export function Settings({ theme, setTheme }: ViewProps) {
  const toast = useToast();
  const [cfg, setCfg] = useState<Config | null>(null);
  useEffect(() => { api<Config>("/api/config").then(setCfg); }, []);
  const patch = async (k: string, v: number | boolean) => {
    try { setCfg(await api<Config>("/api/config", { method: "PATCH", json: { [k]: v } })); }
    catch (e) { toast({ title: "Setting rejected", body: (e as Error).message, kind: "bad" }); }
  };
  return (
    <>
      <Header title="Settings" sub="Changes take effect right away and are remembered next time." />
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>Appearance</h3>
        <div className="seg">{["dark", "light", "system"].map((t) => <button key={t} className={theme === t ? "on" : ""} onClick={() => setTheme(t)}>{t[0].toUpperCase() + t.slice(1)}</button>)}</div>
      </div>
      <div className="grid g2">{cfg && FIELDS.map((g) => (
        <div className="card" key={g.group}>
          <h3>{g.group}</h3>
          <div className="grid" style={{ gap: 10 }}>{g.items.map(([k, label, step]) => typeof cfg[k] === "boolean" ? (
            <label key={k} className="spread" style={{ cursor: "pointer" }}><span>{label}</span><input type="checkbox" checked={!!cfg[k]} onChange={(e) => patch(k, e.target.checked)} /></label>
          ) : (
            <label key={k} className="spread"><span>{label}</span>
              <input type="number" step={step} defaultValue={Number(cfg[k])} style={{ width: 96, textAlign: "right" }}
                onBlur={(e) => Number(e.target.value) !== cfg[k] && patch(k, Number(e.target.value))} /></label>
          ))}</div>
        </div>
      ))}</div>
    </>
  );
}


// ============================================================== DIGITAL TWIN
type Preview = Record<string, { prims: Prim[]; slice: [number, number, number, number][] }>;
export function DigitalTwin({ go }: ViewProps) {
  const [, setCfg] = useState<Config | null>(null);
  const [h, setH] = useState(0.6);
  const [n, setN] = useState(60);
  useEffect(() => { api<Config>("/api/config").then((c) => { setCfg(c); setH(Number(c.mount_height_m)); }); }, []);
  const prev = useFetch<Preview>(`/api/twin/preview?height_m=${h}`, [h]);
  const saveH = async (v: number) => { setH(v); setCfg(await api<Config>("/api/config", { method: "PATCH", json: { mount_height_m: v } })); };
  return (
    <>
      <Header title="Digital Twin" sub="A simulated copy of your setup: the same LiDAR, a pretend road, and 3D models of cars, motorcycles, bicycles and people. The app practises on it so it can make a first guess before you've labeled anything real." />
      <div className="note" style={{ marginBottom: 14 }}>
        <b>Be honest about what this is.</b> Models trained here learn from <i>simulated</i> scans, and their live results are labelled "ML (digital twin)".
        Their score says how well they do on more simulation, not on your real road. Train on "Both" once you've labeled real objects:
        the score on real objects is the one to trust.
      </div>
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="row" style={{ gap: 22 }}>
          <label className="field">How high is the LiDAR above the road?
            <div className="row"><input type="range" min={0.2} max={1.6} step={0.05} value={h} onChange={(e) => setH(Number(e.target.value))} onPointerUp={() => saveH(h)} onKeyUp={() => saveH(h)} />
              <b className="mono">{h.toFixed(2)} m</b></div></label>
          <label className="field">Pretend objects per type
            <div className="seg">{[30, 60, 120].map((v) => <button key={v} className={n === v ? "on" : ""} onClick={() => setN(v)}>{v}</button>)}</div></label>
        </div>
      </div>
      <TrainCard height={h} perClass={n} data="twin" />
      <div className="grid g4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", marginBottom: 14 }}>
        {(["car", "motorcycle", "bicycle", "pedestrian"] as const).map((k) => (
          <div className="card" key={k} style={{ paddingBottom: 10 }}>
            <h3><span className="cat" style={{ background: CAT_COLOR[k] }} />{k}</h3>
            {prev.data ? <Iso3D prims={prev.data[k].prims} slices={prev.data[k].slice} sliceZ={h} color={CAT_COLOR[k]} height={170} /> : <div style={{ height: 170 }} />}
            <div className="faint" style={{ fontSize: 11.5 }}>
              {prev.data && (prev.data[k].slice.length ? `${prev.data[k].slice.length} piece${prev.data[k].slice.length > 1 ? "s" : ""} cut by the scan at ${h.toFixed(2)} m` : `the scan passes above it at ${h.toFixed(2)} m`)}
            </div>
          </div>
        ))}
      </div>
      <div className="faint" style={{ fontSize: 11.5, margin: "-6px 0 14px" }}>Orange is the LiDAR's scan plane: the only slice of each object the real sensor ever sees. These 3D shapes are simplified models, not measurements.</div>
      <div className="grid g2">
        <SyntheticDatasetCard />
        <div className="card">
          <h3>Results</h3>
          <div className="muted" style={{ fontSize: 12.5 }}>Accuracy, mix-ups and which model is in use are on the Machine Learning page.</div>
          <button className="btn sm" style={{ marginTop: 10 }} onClick={() => go("ml")}>Open Machine Learning</button>
        </div>
      </div>
    </>
  );
}

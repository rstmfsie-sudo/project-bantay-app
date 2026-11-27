import { useCallback, useState } from "react";
import { api, CAT_COLOR, CATEGORIES, fmt, METHOD_LABEL, setSource, useLive, useToast } from "../api";
import ScanCanvas, { DEFAULT_LAYERS, Layers } from "../ScanCanvas";
import { ReplayBar, SourceCard } from "../ReplayBar";
import TwinView from "../TwinView";

const LAYER_NAMES: [keyof Layers, string][] = [
  ["rejected", "Rejected raw"], ["filtered", "Filtered points"], ["clusters", "Clusters"],
  ["ego", "Your motorcycle"], ["tracks", "Tracked objects"], ["solid3d", "3D objects"], ["labels", "Labels"], ["trails", "Trails"],
];

export default function LiveScan() {
  const { frameRef, rfRef, frame, status } = useLive();
  const toast = useToast();
  const [range, setRange] = useState(() => Number(localStorage.getItem("range") || 8));
  const [layers, setLayers] = useState<Layers>(() => ({ ...DEFAULT_LAYERS, ...JSON.parse(localStorage.getItem("layers") || "{}") }));
  const [persp, setPersp] = useState(false);
  const [view, setView] = useState<"2d" | "3d">(() => (new URLSearchParams(location.search).get("scanview") as "2d" | "3d") || (localStorage.getItem("scanView") as "2d" | "3d") || "2d");
  const pickView = (v: "2d" | "3d") => { setView(v); localStorage.setItem("scanView", v); };
  const [reset, setReset] = useState(0);
  const [sel, setSel] = useState<number | null>(null);
  const [follow, setFollow] = useState(true);
  const onSelect = useCallback((id: number | null) => setSel(id), []);
  const track = frame?.tracks.find((t) => t.id === sel);
  const toggle = (k: keyof Layers) => setLayers((l) => { const n = { ...l, [k]: !l[k] }; localStorage.setItem("layers", JSON.stringify(n)); return n; });

  const label = async (cat: string) => {
    if (!track) return;
    try {
      await api("/api/dataset", { method: "POST", json: { track_id: track.id, label: cat, follow } });
      toast({ title: `Labeled #${track.id} as ${cat}`, kind: "ok",
        body: follow ? "Following: a sample every 0.5 s until it leaves the scan" : `1 sample, ${track.features.n_points} points` });
    } catch (e) { toast({ title: "Could not save sample", body: String((e as Error).message), kind: "bad" }); }
  };

  const noData = !frame;
  const following = status?.following.find((f) => f.track_id === track?.id);
  return (
    <div className="live-layout">
      <div className={`scanwrap ${persp ? "persp" : ""}`}>
        {view === "3d" ? <TwinView frameRef={frameRef} rfRef={rfRef} rangeMax={range} showEgo={layers.ego} /> :
        <ScanCanvas frameRef={frameRef} rfRef={rfRef} rangeMax={range} layers={layers} selected={sel} onSelect={onSelect} resetKey={reset} />}
        <div className="overlay" style={{ left: 14, top: 14 }}>
          <div className="seg">{[4, 8, 12].map((r) => (
            <button key={r} className={range === r ? "on" : ""} onClick={() => { setRange(r); localStorage.setItem("range", String(r)); }}>{r} m</button>
          ))}</div>
          <div className="seg">
            <button className={view === "2d" ? "on" : ""} onClick={() => pickView("2d")}>2D</button>
            <button className={view === "3d" ? "on" : ""} onClick={() => pickView("3d")} title="Digital-twin 3D view">3D twin</button>
          </div>
          {view === "2d" && <>
            <button className="btn sm" onClick={() => setReset((x) => x + 1)}>Reset view</button>
            <button className={`btn sm ${persp ? "primary" : ""}`} onClick={() => setPersp((p) => !p)} title="Decorative tilt only. The data stays 2D.">Perspective</button>
          </>}
        </div>
        <div style={{ position: "absolute", right: 22, top: 20 }}>
          <span className="stamp">{frame?.source === "simulation" ? "simulation" : frame?.source === "replay" ? "replay · recorded" : "2D · single plane"}</span>
        </div>
        <ReplayBar status={status} />
        <div className="badge2d">
          {view === "3d" ? "3D digital-twin view: each object is shown as the 3D model of its detected type at its tracked spot. The shapes are illustrations; the LiDAR measures a flat slice. Drag to turn · scroll to zoom" : <>
          {persp ? "Tilted just for looks: the measurements are still a flat 2D slice. " : "Looking down from above. "}
          {layers.solid3d && "3D blocks use each type's typical height (the LiDAR measures a flat slice). "}
          Scroll to zoom · drag to move · click something to see what it is</>}
        </div>
        {noData && (
          <div className="empty" style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
            <b>Waiting for the LiDAR</b>
            <span>{status?.lidar.message ?? "Starting up…"}</span>
            <span className="faint">Nothing here is made up: no sensor, no dots.</span>
            <button className="btn sm primary" style={{ pointerEvents: "auto", marginTop: 6 }} onClick={() => setSource("simulation")}>No LiDAR with you? Try the simulation</button>
          </div>
        )}
      </div>
      <aside className="live-side">
        <SourceCard status={status} />
        <div className="card">
          <h3>Scan</h3>
          <div className="kv">
            <span>Source</span><span>{frame?.source === "simulation" ? "SIMULATION" : frame?.source === "replay" ? "REPLAY" : status?.lidar.state ?? "—"}</span>
            <span>Raw returns</span><span>{frame?.n_raw ?? "—"}</span>
            <span>Valid (filtered)</span><span>{frame?.n_valid ?? "—"}</span>
            <span>Foreground</span><span>{frame?.n_foreground ?? "—"}</span>
            <span>Clusters</span><span>{frame?.clusters.length ?? "—"}</span>
            <span>Tracks</span><span>{frame?.tracks.length ?? "—"}</span>
            <span>Background</span><span>{!status?.background.enabled ? "off" : status.background.ready ? "adaptive · ready" : `warming up ${Math.round(status.background.progress * 100)}%`}</span>
          </div>
        </div>
        <div className="card">
          <h3>Layers</h3>
          <div className="grid" style={{ gap: 7 }}>
            {LAYER_NAMES.map(([k, n]) => (
              <label key={k} className="spread" style={{ cursor: "pointer" }}><span>{n}</span>
                <input type="checkbox" checked={layers[k]} onChange={() => toggle(k)} /></label>
            ))}
          </div>
        </div>
        <div className="card">
          <h3>Selected object</h3>
          {!track ? <div className="muted" style={{ fontSize: 12.5 }}>Click any box in the scan to see what it is, and tell the app what it really was.</div> : (
            <>
              <div className="spread" style={{ marginBottom: 10 }}>
                <b>#{track.id} <span className="cat" style={{ background: CAT_COLOR[track.category] }} />{track.category}</b>
                <span className="chip">{METHOD_LABEL[track.method]}</span>
              </div>
              <div className="kv" style={{ fontSize: 12.5 }}>
                <span>Distance</span><span>{fmt.m(track.range)}</span>
                <span>Bearing</span><span>{fmt.deg(track.bearing)}</span>
                <span>Max extent</span><span>{fmt.m(track.max_length)}</span>
                <span>Points</span><span>{track.features.n_points}</span>
                <span>Speed (est.)</span><span>{track.speed == null ? "insufficient data" : `${track.speed.toFixed(1)} m/s`}</span>
                <span>Confidence</span><span>{track.confidence == null ? "n/a (rules)" : track.confidence.toFixed(2)}</span>
                <span>Risk</span><span>{track.risk}</span>
              </div>
              {track.note && <div className="faint" style={{ fontSize: 11.5, marginTop: 8 }}>{track.note}</div>}
              {frame?.source === "simulation" ? <div className="note plain" style={{ marginTop: 14, fontSize: 12 }}>
                This is a pretend object from the simulation, so it can't be labeled. Only real objects go into your dataset.</div> : <>
              <h3 style={{ marginTop: 14 }}>It's really a…</h3>
              <label className="row" style={{ cursor: "pointer", fontSize: 12, marginBottom: 8 }}>
                <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
                Follow: keep capturing it as it moves
              </label>
              <div className="row">{CATEGORIES.map((c) => <button key={c} className={`btn sm ${following?.label === c ? "primary" : ""}`} onClick={() => label(c)}>{c}</button>)}</div>
              {following && <div className="row" style={{ marginTop: 10, fontSize: 12 }}>
                <span className="dot bad pulse" /> following as <b>{following.label}</b> · {following.samples} samples
                <button className="btn sm ghost" onClick={() => api(`/api/dataset/follow/${following.track_id}`, { method: "DELETE" })}>Stop</button>
              </div>}
              <div className="faint" style={{ fontSize: 11.5, marginTop: 10 }}>Label only what you can confirm with your own eyes or synchronized video.</div>
              </>}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

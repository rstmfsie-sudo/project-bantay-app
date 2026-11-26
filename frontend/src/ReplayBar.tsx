import { useEffect, useState } from "react";
import { api, Config, ReplayState, Status, useToast } from "./api";

const ctl = (action: string, value = 1) => api<ReplayState>("/api/replay/control", { method: "POST", json: { action, value } });
const clock = (t?: number | null) =>
  t == null ? "—" : new Date(t * 1000).toLocaleTimeString([], { hour12: false }) + "." + Math.floor((t % 1) * 10);
const offset = (t?: number | null, t0?: number | null) => {
  if (t == null || t0 == null) return "0:00.0";
  const s = Math.max(0, t - t0);
  return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
};

/** Transport controls for labeling from a recording. The clock is the RECORDED time, so it can be
 * matched against a phone video filmed at the same time. */
export function ReplayBar({ status }: { status: Status | null }) {
  const r = status?.replay;
  const [drag, setDrag] = useState<number | null>(null);
  if (!r || status?.source !== "replay" || !r.total) return null;
  const paused = r.paused || r.message === "finished";
  return (
    <div className="overlay" style={{ left: 14, right: 14, bottom: 40, padding: "8px 12px", gap: 10 }}>
      <button className="btn sm" title="Step back" onClick={async () => { await ctl("seek", r.pos - 2); await ctl("step", 1); }}>◀︎◀︎</button>
      <button className="btn sm primary" style={{ minWidth: 70 }} onClick={() => ctl(paused ? "play" : "pause")}>{paused ? "▶ Play" : "❚❚ Pause"}</button>
      <button className="btn sm" title="Step forward one scan" onClick={() => ctl("step", 1)}>▶︎▶︎</button>
      <input type="range" min={0} max={Math.max(r.total - 1, 0)} value={drag ?? r.pos} style={{ flex: 1 }}
        aria-label="Seek in recording"
        onChange={(e) => setDrag(Number(e.target.value))}
        onPointerUp={async () => { if (drag != null) { await ctl("seek", drag); await ctl("step", 1); setDrag(null); } }}
        onKeyUp={async () => { if (drag != null) { await ctl("seek", drag); await ctl("step", 1); setDrag(null); } }} />
      <span className="mono" style={{ fontSize: 11.5, whiteSpace: "nowrap" }} title="Recorded wall-clock time (match this to your video)">
        {offset(r.rec_t, r.rec_t0)} · <b>{clock(r.rec_t)}</b></span>
      <div className="seg">{[0.25, 0.5, 1, 2].map((v) => (
        <button key={v} className={r.speed === v ? "on" : ""} onClick={() => ctl("speed", v)}>{v}×</button>))}</div>
    </div>
  );
}

/** Choose live sensor vs a recording, right where labeling happens. */
export function SourceCard({ status }: { status: Status | null }) {
  const toast = useToast();
  const [recs, setRecs] = useState<{ name: string; bytes: number }[]>([]);
  const [cfg, setCfg] = useState<Config | null>(null);
  useEffect(() => {
    api<{ name: string; bytes: number }[]>("/api/recordings").then(setRecs);
    api<Config>("/api/config").then(setCfg);
  }, [status?.recording]);
  const patch = async (p: Config) => {
    try { setCfg(await api<Config>("/api/config", { method: "PATCH", json: p })); }
    catch (e) { toast({ title: "Could not switch source", body: (e as Error).message, kind: "bad" }); }
  };
  const replay = cfg?.source === "replay", sim = cfg?.source === "simulation";
  return (
    <div className="card">
      <h3>Source</h3>
      <div className="seg" style={{ marginBottom: 10 }}>
        <button className={!replay && !sim ? "on" : ""} onClick={() => patch({ source: "serial" })}>Live sensor</button>
        <button className={sim ? "on" : ""} onClick={() => patch({ source: "simulation" })} title="Pretend traffic from the digital twin">Simulation</button>
        <button className={replay ? "on" : ""} disabled={!recs.length}
          onClick={() => patch({ source: "replay", replay_file: String(cfg?.replay_file || recs[0]?.name || "") })}>Recording</button>
      </div>
      {replay ? (
        <select value={String(cfg?.replay_file ?? "")} onChange={(e) => patch({ replay_file: e.target.value })} style={{ width: "100%" }}>
          {recs.map((r) => <option key={r.name} value={r.name}>{r.name} ({(r.bytes / 1e6).toFixed(1)} MB)</option>)}
        </select>
      ) : sim ? (
        <div className="muted" style={{ fontSize: 12 }}>Pretend cars, motorcycles, bicycles and people from the digital twin, so you can use the app anywhere.
          Nothing is saved or labeled.</div>
      ) : (
        <div className="muted" style={{ fontSize: 12 }}>
          {recs.length ? `${recs.length} recording(s) available to label calmly later.` : "No recordings yet: turn on Record raw scans in Hardware."}
        </div>
      )}
      {replay && <div className="faint" style={{ fontSize: 11.5, marginTop: 8 }}>Pause, click an object, label it. Replays are not added to Data History.</div>}
    </div>
  );
}

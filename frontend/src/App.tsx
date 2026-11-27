import { useCallback, useEffect, useRef, useState } from "react";
import { api, fmt, LiveCtx, lidarState, NavCtx, Status, Toast, ToastCtx, useLiveConnection } from "./api";
import { Analytics, DatasetManager, Detection, DigitalTwin, Hardware, History, MachineLearning, Overview, Settings, Tracking } from "./views/Views";
import { useAlertSound } from "./alerts";
import LiveScan from "./views/LiveScan";
import VehicleView from "./views/VehicleView";

const I = (d: string) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
);
const NAV = [
  { group: "Monitor", items: [
    { id: "overview", label: "Overview", icon: I("M4 13h6V4H4zM14 20h6v-9h-6zM4 20h6v-3H4zM14 7h6V4h-6z") },
    { id: "vehicle", label: "Vehicle", icon: I("M5 17a2 2 0 1 0 4 0a2 2 0 1 0-4 0M15 17a2 2 0 1 0 4 0a2 2 0 1 0-4 0M7 17l3-7h4l3 7M10 10l-1-3h-2M14 10l2-3h2") },
    { id: "live", label: "Live Scan", icon: I("M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0M12 12l6-6") },
    { id: "detection", label: "Object Detection", icon: I("M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M9 9h6v6H9z") },
    { id: "tracking", label: "Tracking", icon: I("M3 17c3-1 4-8 8-8s5 6 10 4M18 10l3 3-3 3") },
  ]},
  { group: "Research", items: [
    { id: "analytics", label: "Analytics", icon: I("M4 20V10M10 20V4M16 20v-7M22 20H2") },
    { id: "history", label: "Data History", icon: I("M12 7v5l3 2M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5") },
    { id: "dataset", label: "Dataset Manager", icon: I("M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3") },
    { id: "twin", label: "Digital Twin", icon: I("M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zM12 12l8-4.5M12 12v9M12 12L4 7.5") },
    { id: "ml", label: "Machine Learning", icon: I("M12 3v3M12 18v3M3 12h3M18 12h3M7 7h10v10H7zM10 10h4v4h-4z") },
  ]},
  { group: "System", items: [
    { id: "hardware", label: "Hardware", icon: I("M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3M6 6h12v12H6z") },
    { id: "settings", label: "Settings", icon: I("M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.8 1.2V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-2.8-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 3 14H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.2-2.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10 3V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.8 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 21 10h.1a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.6 1z") },
  ]},
];
const ALL = NAV.flatMap((g) => g.items);

export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 256 256" aria-hidden>
      <path fill="#C96F4A" d="M36 204V144A92 92 0 0 1 220 144V204H176V144A48 48 0 0 0 80 144V204Z" />
      <circle fill="#E58A52" cx="128" cy="144" r="26" />
    </svg>
  );
}

type Check = { label: string; state: "run" | "ok" | "warn" | "bad"; msg?: string };

function Boot({ onDone }: { onDone: () => void }) {
  const [checks, setChecks] = useState<Check[]>([]);
  const [out, setOut] = useState(false);
  useEffect(() => {
    let cancel = false;
    const set = (i: number, c: Check) => !cancel && setChecks((cs) => { const n = [...cs]; n[i] = c; return n; });
    const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
    (async () => {
      set(0, { label: "Waking things up", state: "run" });
      let s: Status | null = null;
      for (let i = 0; i < 20 && !s; i++) { try { s = await api<Status>("/api/status"); } catch { await wait(500); } }
      if (!s) { set(0, { label: "Couldn't start", state: "bad", msg: "The engine didn't answer. Try closing and opening the app again." }); return; }
      set(0, { label: "Engine running", state: "ok", msg: "Everything stays on this computer, no internet needed" });
      await wait(250);
      set(1, { label: "Looking for the LiDAR", state: "run" });
      // give the driver a moment to open the port and receive a revolution
      for (let i = 0; i < 8 && s!.lidar.state !== "scanning" && s!.source === "serial"; i++) { await wait(500); s = await api<Status>("/api/status"); }
      const l = s!.lidar;
      set(1, s!.source === "simulation"
        ? { label: "Simulation mode", state: "warn", msg: "Pretend traffic from the digital twin. No LiDAR needed." }
        : s!.source === "replay"
        ? { label: "Playing a recording", state: "warn", msg: "You're watching saved data, not the live sensor" }
        : l.state === "scanning"
          ? { label: "LiDAR found and spinning", state: "ok", msg: `${l.port} · ${l.freq_measured?.toFixed(1) ?? "?"} turns per second` }
          : { label: "LiDAR not found yet", state: "warn", msg: "Everything else works. Plug it in anytime, or try Simulation in Live Scan." });
      await wait(250);
      set(2, { label: "Preparing the view", state: "run" });
      const ok2d = !!document.createElement("canvas").getContext("2d");
      await wait(200);
      set(2, ok2d ? { label: "View ready", state: "ok" } : { label: "The view can't draw here", state: "bad" });
      set(3, { label: "Checking what it has learned", state: "run" });
      await wait(250);
      const m = s!.ml;
      const tw = s!.twin;
      set(3, m.available
        ? { label: "Trained model ready", state: "ok", msg: m.meta?.validated ? "Validated by you" : "Still experimental" }
        : tw?.available
          ? { label: "Digital-twin model ready", state: "ok", msg: "Learned from simulation; label real objects to check it" }
          : { label: "Nothing learned yet", state: "warn", msg: "Using simple shape rules until you train a model" });
      await wait(700);
      if (!cancel) { setOut(true); setTimeout(onDone, 450); }
    })();
    return () => { cancel = true; };
  }, [onDone]);
  return (
    <div className={`boot ${out ? "out" : ""}`}>
      <div className="boot-card">
        <div className="boot-logo"><div className="ring" /><div className="sweep" /><Logo size={84} /></div>
        <div className="faint mono" style={{ fontSize: 11, letterSpacing: ".3em", marginBottom: 4 }}>PROJECT</div>
        <h1>B.A.N.T.A.Y.</h1>
        <div className="sub motto">Never Ride Blind</div>
        <div className="steps" aria-live="polite">
          {checks.map((c, i) => c && (
            <div className="step" key={i}>
              {c.state === "run" ? <div className="spinner" /> : <span className={`dot ${c.state}`} style={{ marginTop: 5 }} />}
              <div>{c.label}{c.msg && <div className="m">{c.msg}</div>}</div>
            </div>
          ))}
        </div>
        <button className="btn ghost sm" style={{ marginTop: 14 }} onClick={onDone}>Skip</button>
      </div>
    </div>
  );
}

export default function App() {
  const live = useLiveConnection();
  const [booted, setBooted] = useState(false);
  const [view, setView] = useState(() => location.hash.slice(1) || localStorage.getItem("view") || "overview");
  const [theme, setTheme] = useState(() => localStorage.getItem("theme") || "dark");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  useEffect(() => {
    const t = theme === "system" ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : theme;
    document.documentElement.dataset.theme = t;
    localStorage.setItem("theme", theme);
  }, [theme]);
  useEffect(() => localStorage.setItem("view", view), [view]);

  const toast = useCallback((t: Omit<Toast, "id">) => {
    const id = nextId.current++;
    setToasts((ts) => [...ts.slice(-3), { ...t, id }]);
    setTimeout(() => setToasts((ts) => ts.filter((x) => x.id !== id)), 4500);
  }, []);

  // notify on real connection state changes
  const prev = useRef<string | null>(null);
  const st = lidarState(live.status);
  useEffect(() => {
    if (!booted || !live.status) return;
    if (prev.current && prev.current !== st.label) {
      const title = st.cls === "ok" ? "LiDAR is back and scanning" : st.label === "Disconnected" ? "Lost the LiDAR" : `LiDAR: ${st.label}`;
      toast({ title, body: live.status.lidar.message, kind: st.cls || undefined });
    }
    prev.current = st.label;
  }, [st.label, booted]); // eslint-disable-line react-hooks/exhaustive-deps

  const done = useCallback(() => setBooted(true), []);
  const [isApp, setIsApp] = useState(() => "pywebview" in window);
  useEffect(() => {
    const on = () => setIsApp(true);
    window.addEventListener("pywebviewready", on);
    return () => window.removeEventListener("pywebviewready", on);
  }, []);
  const winApi = (fn: string) => (window as any).pywebview?.api?.[fn]?.();
  const prefs = live.status?.alert_prefs;
  useAlertSound(live.alert, !!prefs?.sound, prefs?.volume ?? 0.3);
  const glow = prefs?.light === false ? 0 : live.alert.S;
  const s = live.status;
  const page = ALL.find((n) => n.id === view) ?? ALL[0];
  const V = { overview: Overview, vehicle: VehicleView, live: LiveScan, detection: Detection, tracking: Tracking, analytics: Analytics,
    history: History, dataset: DatasetManager, twin: DigitalTwin, ml: MachineLearning, hardware: Hardware, settings: Settings }[page.id]!;

  return (
    <LiveCtx.Provider value={live}>
      <NavCtx.Provider value={setView}>
      <ToastCtx.Provider value={toast}>
        {!booted && <Boot onDone={done} />}
        <div className={`desk ${isApp ? "app" : ""}`}>
          <div className={`window ${glow ? `alert-${glow}` : ""}`}>
            <header className="titlebar pywebview-drag-region">
              {isApp && <div className="wc">
                <button className="close" data-g="×" aria-label="Close" onClick={() => winApi("close")} />
                <button className="min" data-g="–" aria-label="Minimize" onClick={() => winApi("minimize")} />
                <button className="max" data-g="+" aria-label="Maximize" onClick={() => winApi("toggle_maximize")} />
              </div>}
              <div className="brand"><Logo /><span className="brand-name"><small>PROJECT</small>B.A.N.T.A.Y.</span></div>
              <div className="title">{page.label}</div>
              <div className="row">
                <span className={`lamp ${live.alert.S ? `l${live.alert.S}` : ""}`} role="status"
                  title={live.alert.S ? `Heads up: ${live.alert.reasons.join(", ")}` : "All clear"}
                  aria-label={live.alert.S === 2 ? "Warning: something is very close" : live.alert.S === 1 ? "Caution: something is near" : "All clear"} />
                {s?.source === "replay" && <span className="chip accent">REPLAY · recorded data</span>}
                {s?.recording && <span className="chip accent"><span className="dot bad pulse" /> REC</span>}
                <span className="chip"><span className={`dot ${st.cls} ${st.cls === "ok" ? "pulse" : ""}`} /> {st.label}</span>
              </div>
            </header>
            <div className="body">
              <nav className="sidebar" aria-label="Main">
                {NAV.map((g) => (
                  <div key={g.group}>
                    <h6>{g.group}</h6>
                    {g.items.map((n) => (
                      <button key={n.id} className={`nav ${view === n.id ? "active" : ""}`} onClick={() => setView(n.id)}
                        aria-current={view === n.id ? "page" : undefined} title={n.label}>
                        {n.icon}<span>{n.label}</span>
                      </button>
                    ))}
                  </div>
                ))}
              </nav>
              <main className={`main ${page.id === "live" ? "flush" : ""}`} key={page.id}>
                <V go={setView} theme={theme} setTheme={setTheme} />
              </main>
            </div>
            <footer className="statusbar">
              <span><span className={`dot ${live.connected ? "ok" : "bad"}`} /> {live.connected ? "Connected" : "Reconnecting…"}</span>
              <span className="mono">{s?.lidar.port ?? "no port"}</span>
              <span className="mono">{s?.lidar.freq_measured ? `${s.lidar.freq_measured.toFixed(1)} Hz` : "— Hz"}</span>
              <span className="mono">{s ? `${(s.lidar.bytes_per_s / 1024).toFixed(1)} KB/s` : ""}</span>
              <span className="mono">{s?.processing.ms != null ? `${s.processing.ms.toFixed(1)} ms/scan` : ""}</span>
              <span className="sp" />
              <span>{s?.twin?.active && s.twin.available ? "Digital-twin model" : s?.ml.active && s.ml.available ? `Trained model (${s.ml.meta?.validated ? "validated" : "experimental"})` : "Shape rules"}</span>
              <span className="mono">up {s ? fmt.dur(s.uptime_s) : "—"}</span>
            </footer>
          </div>
        </div>
        {s?.closing_in != null && (
          <div className="overlay" role="alert" style={{ position: "fixed", left: "50%", top: 70, transform: "translateX(-50%)", zIndex: 45, padding: "10px 16px", gap: 12 }}>
            <span className="dot bad pulse" />
            <span><b>The LiDAR was unplugged.</b> Closing in <span className="mono">{Math.ceil(s.closing_in)} s</span>. Plug it back in and I'll keep going.</span>
          </div>
        )}
        <div className="toasts" aria-live="polite">
          {toasts.map((t) => (
            <div className="toast" key={t.id}>
              <span className={`dot ${t.kind ?? ""}`} style={{ marginTop: 5 }} />
              <div><b>{t.title}</b>{t.body && <span>{t.body}</span>}</div>
            </div>
          ))}
        </div>
      </ToastCtx.Provider>
      </NavCtx.Provider>
    </LiveCtx.Provider>
  );
}

export type ViewProps = { go: (v: string) => void; theme: string; setTheme: (t: string) => void };

import { createContext, useContext, useEffect, useRef, useState } from "react";

export type Track = {
  id: number; x: number; y: number; range: number; bearing: number;
  speed: number | null; heading: number | null; ttc: number | null; risk: "CLEAR" | "CAUTION" | "WARNING";
  duration: number; age: number; missed: number; max_length: number; features: Record<string, number>;
  category: string; method: string; confidence: number | null; note: string;
  trail: [number, number][]; box: [number, number, number, number]; cluster_id: number;
};
export type Frame = {
  t: number; n_raw: number; n_valid: number; n_foreground: number; source: "serial" | "replay";
  points: [number, number, number][]; rejected: [number, number][];
  clusters: { id: number; xmin: number; ymin: number; xmax: number; ymax: number; cx: number; cy: number }[];
  tracks: Track[];
  replay?: ReplayState | null;
};
export type ReplayState = { file: string | null; pos: number; total: number; message: string;
  paused?: boolean; speed?: number; rec_t?: number | null; rec_t0?: number | null };
export type Status = {
  uptime_s: number;
  lidar: {
    state: string; port: string | null; message: string; model: number | null; firmware: string | null;
    hardware: number | null; serial: string | null; health: number | null; freq_reported: number | null;
    freq_measured: number | null; bytes_per_s: number; packets_ok: number; packets_bad: number; scans: number;
    last_scan_t: number | null; stale: boolean;
  };
  pico: { detected: boolean; port: string | null; role: string };
  replay: ReplayState;
  ports: { device: string; description: string; vid: number | null; pid: number | null }[];
  processing: { ms: number | null; dropped_scans: number; queue: number };
  counts: { objects: number; classified: number; unknown: number };
  background: { ready: boolean; progress: number; enabled: boolean; absorb_s: number };
  ml: { available: boolean; active: boolean; meta: MLMeta | null };
  nn?: { installed: boolean; available: boolean; active: boolean; meta: MLMeta | null };
  source: string; recording: boolean;
  dataset: Record<string, { samples: number; objects: number }>;
  closing_in: number | null;
  alert: Alert;
  alert_prefs: { sound: boolean; volume: number; light: boolean };
  rangefinders: { state: string; port: string | null; message: string; rate_hz: number; frames_bad: number };
  twin?: { available: boolean; active: boolean; meta: TwinMeta | null; job: { running: boolean; progress: number; message: string } };
  following: { track_id: number; label: string; samples: number }[];
};
export type MLMeta = {
  version: string; algorithm: string; features: string[]; classes: string[]; samples: Record<string, number>;
  objects: Record<string, number>; cv_grouped_by: string;
  per_class: Record<string, { precision: number; recall: number; f1: number; samples: number }>;
  cv_folds: number; cv_accuracy: number; confusion_matrix: number[][]; feature_importance?: Record<string, number>;
  validated: boolean; trained_at: number; note: string;
};
export type Config = Record<string, number | string | boolean>;
export type Alert = { L: number; R: number; S: number; reasons: string[] };
export type RF = { state: string; beams: { side: "L" | "C" | "R"; angle: number; m: number | null; strength: number | null }[] };
export type TwinMeta = MLMeta & { mount_height_m: number; features: string[];
  sim_to_real: { accuracy: number; samples: number; confusion_matrix: number[][] } | null };

export const CATEGORIES = ["car", "motorcycle", "bicycle", "pedestrian", "unknown"] as const;
// Contrasting accents (blue / green / purple) next to the warm palette so categories never blur together.
export const CAT_COLOR: Record<string, string> = {
  car: "#E58A52", motorcycle: "#7EA6E0", bicycle: "#8CC79A", pedestrian: "#C3A1EC", unknown: "#9A938B",
};
export const METHOD_LABEL: Record<string, string> = {
  detection: "Detection only", rule_based: "Rule-based", ml_experimental: "ML (experimental)", ml_validated: "ML (validated)",
  ml_synthetic: "ML (digital twin)",
};

export async function api<T = any>(path: string, opts: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = opts;
  const r = await fetch(path, {
    ...rest,
    headers: json !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try { msg = (await r.json()).detail ?? msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}

/** One WebSocket for the whole app. Frames go to a ref (read by the canvas each animation frame), so
 * high-rate scans never force React re-renders; a throttled copy is published for tables. */
export function useLiveConnection() {
  const frameRef = useRef<Frame | null>(null);
  const rfRef = useRef<RF | null>(null);
  const [rf, setRf] = useState<RF | null>(null);
  const [alert, setAlert] = useState<Alert>({ L: 0, R: 0, S: 0, reasons: [] });
  const [frame, setFrame] = useState<Frame | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    let ws: WebSocket | null = null, retry: number | undefined, alive = true, lastPub = 0, lastRf = 0;
    const open = () => {
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => { setConnected(false); if (alive) retry = window.setTimeout(open, 1500); };
      ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.type === "frame") {
          frameRef.current = m.frame;
          const now = performance.now();
          if (now - lastPub > 250) { lastPub = now; setFrame(m.frame); }
        } else if (m.type === "status") setStatus(m.status);
        else if (m.type === "alert") setAlert(m.alert);
        else if (m.type === "rf") {
          rfRef.current = m.rf;
          const now = performance.now();
          if (now - lastRf > 300) { lastRf = now; setRf(m.rf); }
        }
      };
    };
    open();
    return () => { alive = false; clearTimeout(retry); ws?.close(); };
  }, []);
  return { frameRef, frame, status, connected, rfRef, rf, alert };
}

export type Live = ReturnType<typeof useLiveConnection>;
export const LiveCtx = createContext<Live>(null as unknown as Live);
export const useLive = () => useContext(LiveCtx);

type Toast = { id: number; title: string; body?: string; kind?: "ok" | "warn" | "bad" };
export const ToastCtx = createContext<(t: Omit<Toast, "id">) => void>(() => {});
export const useToast = () => useContext(ToastCtx);
export type { Toast };

export const fmt = {
  m: (v: number | null | undefined, d = 2) => (v == null ? "—" : `${v.toFixed(d)} m`),
  deg: (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(1)}°`),
  s: (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(1)} s`),
  time: (t: number) => new Date(t * 1000).toLocaleString(),
  dur: (s: number) => {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = Math.floor(s % 60);
    return h ? `${h}h ${m}m` : m ? `${m}m ${x}s` : `${x}s`;
  },
};

export function lidarState(s: Status | null): { label: string; cls: "ok" | "warn" | "bad" | "" } {
  if (!s) return { label: "Backend offline", cls: "bad" };
  if (s.source === "replay") return { label: "Replay", cls: "warn" };
  const st = s.lidar.state;
  if (st === "scanning" && !s.lidar.stale) return { label: "Scanning", cls: "ok" };
  if (st === "connecting") return { label: "Connecting", cls: "warn" };
  if (st === "error") return { label: "Error", cls: "bad" };
  return { label: "Disconnected", cls: "" };
}

/** Navigation for headers' "NEXT: … →" links. */
export const NavCtx = createContext<(view: string) => void>(() => {});

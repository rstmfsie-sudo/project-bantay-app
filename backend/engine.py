"""Glue: acquisition thread -> bounded queue -> processing thread -> latest frame for WebSockets.

The serial reader only enqueues; if processing falls behind, the oldest scan is dropped (counted),
so acquisition is never blocked by processing or visualization.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from pathlib import Path

from .classify import ML_FEATURES, MLClassifier, feature_vector, rule_classify
from .config import DATA, Config
from .db import DB
from .pointnet import PointNetClassifier
from .rangefinders import RangefinderLink
from . import twin
from .twin import SimulationThread
from . import vehicle
from .processing import Background, process
from .tracking import Tracker
from .ydlidar import LidarDriver, Scan

log = logging.getLogger("bantay.engine")


class Engine:
    def __init__(self):
        self.cfg = Config()
        self.db = DB()
        self.bg = Background()
        self.tracker = Tracker()
        self.ml = MLClassifier()
        self.nn = PointNetClassifier()
        self.ml_job = {"running": False, "progress": 0.0, "message": "idle", "results": {}}
        if self.cfg.get()["classifier"] == "twin":        # the separate twin model became Random Forest data="twin"
            self.cfg.update({"classifier": "ml"})
        self.q: queue.Queue[Scan] = queue.Queue(maxsize=3)
        self.dropped = 0
        self.started = time.time()
        self.frame: dict | None = None
        self.seq = 0
        self.proc_ms = None
        self.counts = {"objects": 0, "classified": 0, "unknown": 0}
        self._counted: dict[int, str] = {}
        # label & follow: track_id -> [label, last_capture_t, n_captured]
        self.follow: dict[int, list] = {}
        self.reset_tracking = False   # set by replay seek; applied on the processing thread
        self.closing_in: float | None = None   # countdown shown in the UI before auto-exit
        self._was_sim = False
        self.on_exit = None                    # set by the desktop app to close its window
        self._rec_file = None
        self.replay = ReplayThread(self)
        self.sim = SimulationThread(self)
        self.driver = LidarDriver(self.cfg.get, self.on_scan)
        self.rf = RangefinderLink(self.cfg.get)
        self.alert = {"L": 0, "R": 0, "S": 0, "reasons": [], "monitor": None}
        self.alert_seq = 0

    def start(self):
        if self.driver.is_alive():   # lifespan may run more than once (tests)
            return
        self.db.purge(self.cfg.get()["retention_days"])
        threading.Thread(target=self._process_loop, daemon=True, name="processing").start()
        threading.Thread(target=self._housekeeping, daemon=True, name="housekeeping").start()
        threading.Thread(target=self._unplug_watch, daemon=True, name="unplug-watch").start()
        threading.Thread(target=self._alert_loop, daemon=True, name="alerts").start()
        self.rf.start()
        self.driver.start()
        self.replay.start()
        self.sim.start()

    # ------------------------------------------------------------ acquisition side
    def on_scan(self, scan: Scan):
        try:
            self.q.put_nowait(scan)
        except queue.Full:
            try:
                self.q.get_nowait()
                self.dropped += 1
            except queue.Empty:
                pass
            self.q.put_nowait(scan)

    # ------------------------------------------------------------ processing side
    def _process_loop(self):
        while True:
            scan = self.q.get()
            try:
                self._process(scan)
            except Exception:
                log.exception("processing failed")

    def _record(self, scan, cfg):
        if not cfg["record"]:
            if self._rec_file:
                self._rec_file.close(); self._rec_file = None
            return
        if not self._rec_file:
            path = DATA / "recordings" / time.strftime("scan-%Y%m%d-%H%M%S.jsonl")
            self._rec_file = open(path, "a", encoding="utf-8")
        self._rec_file.write(json.dumps(scan.to_json()) + "\n")

    def _process(self, scan: Scan):
        t0 = time.perf_counter()
        cfg = self.cfg.get()
        replay = cfg["source"] == "replay"
        sim = cfg["source"] == "simulation"
        if sim != self._was_sim:            # switching to/from simulation: start tracking and background fresh
            self._was_sim, self.reset_tracking = sim, True
        if self.reset_tracking:
            self.reset_tracking = False
            self.tracker.reset()
            self.follow.clear()
            self.bg.reset()
        if not (replay or sim):
            self._record(scan, cfg)   # never re-record a replay, never record simulation
        frame, clusters = process(scan, cfg, self.bg)
        tracks = []
        use_ml = cfg["classifier"] == "ml" and self.ml.available
        use_nn = cfg["classifier"] == "nn" and self.nn.available
        for tr in self.tracker.step(clusters, scan.t, cfg):
            d = self.tracker.describe(tr, cfg)
            if use_nn:
                tr.category, tr.confidence, tr.method = self.nn.predict(tr.cluster["points"], feature_vector(d),
                                                                        cfg["ml_min_confidence"])
                tr.note = "network probability (uncalibrated)"
            elif use_ml:
                tr.category, tr.confidence, tr.method = self.ml.predict(d, cfg["ml_min_confidence"])
                tr.note = "model probability (uncalibrated)"
            else:
                (tr.category, tr.note), tr.confidence, tr.method = rule_classify(d), None, "rule_based"
            d.update(category=tr.category, method=tr.method, confidence=tr.confidence, note=tr.note)
            tracks.append(d)
            self._count(tr)
            if not sim:                     # simulated objects never enter the real dataset
                self._follow_capture(tr, d, scan.t, frame_source=cfg["source"])
            # history holds live sessions only: replaying a file twice must not duplicate rows
            if not (replay or sim) and scan.t - tr.last_logged >= cfg["log_interval_s"]:
                tr.last_logged = scan.t
                self.db.add_detection({
                    "ts": scan.t, "track_id": tr.id, "category": tr.category, "method": tr.method,
                    "confidence": tr.confidence,
                    "model_version": self.nn.meta["version"] if use_nn else self.ml.meta["version"] if use_ml else "rules-v1",
                    "distance": d["range"], "angle": d["bearing"], "x": d["x"], "y": d["y"],
                    "speed": d["speed"], "length": d["max_length"],
                    "n_points": int(d["features"]["n_points"]), "risk": d["risk"]})
        live = {tr.id for tr in self.tracker.tracks}
        self.follow = {k: v for k, v in self.follow.items() if k in live}
        self.proc_ms = (time.perf_counter() - t0) * 1000 if self.proc_ms is None \
            else 0.9 * self.proc_ms + 0.1 * (time.perf_counter() - t0) * 1000
        frame.update(t=scan.t, t_wall=time.time(), front_deg=cfg["angle_offset_deg"],   # where the sensor's front points on the plot
                     clusters=[{k: c[k] for k in ("id", "xmin", "ymin", "xmax", "ymax", "cx", "cy")}
                                         for c in clusters], tracks=tracks,
                     source="simulation" if sim else "replay" if replay else "serial",
                     replay=self.replay.state() if replay else None)
        self.frame = frame
        self.seq += 1

    def _count(self, tr):
        prev = self._counted.get(tr.id)
        if prev is None:
            self.counts["objects"] += 1
        else:
            self.counts["classified" if prev != "unknown" else "unknown"] -= 1
        self.counts["classified" if tr.category != "unknown" else "unknown"] += 1
        self._counted[tr.id] = tr.category

    def _housekeeping(self):
        while True:
            time.sleep(3600)
            self.db.purge(self.cfg.get()["retention_days"])
            self._counted = dict(list(self._counted.items())[-5000:])   # bound memory on long runs

    # ------------------------------------------------------------ machine learning
    NO_INTENSITY = [i for i, n in enumerate(ML_FEATURES) if n != "mean_intensity"]   # the twin does not model intensity

    def start_ml_job(self, data: str, models: list, height: float, per_class: int, activate: bool):
        if self.ml_job.get("running"):
            raise ValueError("Training is already running")
        self.ml_job = {"running": True, "progress": 0.0, "message": "starting", "data": data, "results": {}}
        threading.Thread(target=self._ml_job, args=(data, models, height, per_class, activate),
                         daemon=True, name="ml-training").start()
        return self.ml_job

    def _ml_job(self, data, models, height, per_class, activate):
        j = self.ml_job
        try:
            X, y, g, P, real = [], [], [], [], []
            if data in ("twin", "both"):
                m = twin.dataset_meta()
                if not m or abs(m["mount_height_m"] - height) > 1e-6 or m["objects_requested_per_class"] != per_class:
                    j["message"] = "simulating the digital twin dataset"
                    twin.build_dataset(height, per_class, progress=lambda f: j.update(progress=0.4 * f))
                ds = twin.load_dataset()
                X += ds["X"]; y += ds["y"]; g += ds["groups"]; P += ds["points"]; real += [False] * len(ds["y"])
            if data in ("real", "both"):
                s = [x for x in self.db.samples() if x["label"] != "unknown" and all(k in x["features"] for k in ML_FEATURES)]
                X += [[x["features"][k] for k in ML_FEATURES] for x in s]
                y += [x["label"] for x in s]; g += [x["object"] for x in s]; P += [x["points"] for x in s]
                real += [True] * len(s)
            if not y:
                raise ValueError("no training data yet: label real objects, or use the digital twin")
            cols = None if data == "real" else self.NO_INTENSITY
            res = {}
            if "rf" in models:
                j.update(message="training the Random Forest", progress=0.42)
                try:
                    res["rf"] = self.ml.train(X, y, g, cols=cols, real=real, data=data)
                except ValueError as e:
                    res["rf"] = {"error": str(e)}
            if "nn" in models:
                if not self.nn.installed:
                    res["nn"] = {"error": "PyTorch is not installed"}
                else:
                    j.update(message="training the neural network (a few minutes on the CPU)", progress=0.5)
                    try:
                        res["nn"] = self.nn.train(P, X, y, g, cols=cols, real=real, data=data,
                                                  progress=lambda f: j.update(progress=0.5 + 0.48 * f))
                    except ValueError as e:
                        res["nn"] = {"error": str(e)}
            # judge on real objects whenever they took part, otherwise on (simulated) grouped CV
            score = lambda m: (m.get("real_cv") or {}).get("accuracy", m.get("cv_accuracy", -1))
            ok = {k: m for k, m in res.items() if "error" not in m}
            best = max(ok, key=lambda k: score(ok[k])) if ok else None
            if activate and best:
                self.cfg.update({"classifier": "ml" if best == "rf" else "nn"})
            summary = {k: ({"error": m["error"]} if "error" in m else
                           {"version": m["version"], "cv_accuracy": m["cv_accuracy"], "real_cv": m.get("real_cv")})
                       for k, m in res.items()}
            name = {"rf": "Random Forest", "nn": "neural network"}.get(best)
            j.update(running=False, progress=1.0, results=summary, best=best,
                     message=(f"done: now using the {name}" if activate and best else "done") if best else
                     "nothing trained: " + "; ".join(m["error"] for m in res.values() if "error" in m))
        except Exception as e:
            log.exception("training failed")
            j.update(running=False, message=f"failed: {e}")

    # ------------------------------------------------------------ alerts
    TRACK_LEVEL = {"CLEAR": 0, "CAUTION": 1, "WARNING": 2}   # time-to-collision risk of tracked objects

    def fresh_points(self, frame, now):
        """LiDAR points only if the frame is fresh; stale scans never drive warnings."""
        return frame["points"] if frame and now - frame.get("t_wall", 0) < 1.5 else None

    def compute_alert(self, cfg, frame, tfl_readings, now):
        """Vehicle obstacle monitoring (SAFE..DANGER per side, from the TF-Lunas and the LiDAR) merged with the
        tracked objects' time-to-collision risk. Returns levels 0..3 for the left, right and overall indicators."""
        mon = vehicle.monitor(cfg, self.fresh_points(frame, now), tfl_readings or {})
        L, R = mon["sides"]["left"]["level"], mon["sides"]["right"]["level"]
        S = mon["overall"]
        reasons = [f"{s} {v['nearest_m']:.2f} m ({v['source']})" for s, v in mon["sides"].items() if v["level"]]
        if frame and now - frame.get("t_wall", 0) < 1.5:
            for t in frame.get("tracks", []):
                lvl = self.TRACK_LEVEL[t["risk"]]
                if not lvl:
                    continue
                vx, vy = vehicle.to_vehicle(t["x"], t["y"], cfg)
                side = vehicle.sector(vx, vy)
                if side == "left":
                    L = max(L, lvl)
                elif side == "right":
                    R = max(R, lvl)
                S = max(S, lvl)
                reasons.append(f"{t['category']} #{t['id']} closing, {side}")
        return {"L": L, "R": R, "S": S, "reasons": reasons[:4], "monitor": mon}

    def _alert_loop(self):
        """Vehicle monitoring at 10 Hz. Must never stop: an exception is logged and the next cycle runs.
        (The Pico drives its own LEDs/buzzer from its own sensors; the app never writes to it.)"""
        while True:
            time.sleep(0.1)
            try:
                cfg, now = self.cfg.get(), time.time()
                a = self.compute_alert(cfg, self.frame, self.rf.readings(now), now)
                if (a["L"], a["R"], a["S"]) != (self.alert["L"], self.alert["R"], self.alert["S"]):
                    self.alert_seq += 1
                self.alert = a
            except Exception:
                log.exception("vehicle monitoring cycle failed")
                time.sleep(1)

    def rf_frame(self):
        """TF-Luna states with their mounting geometry (plot frame), the vehicle outline, and the monitor result."""
        cfg, now = self.cfg.get(), time.time()
        geo = vehicle.sensor_geometry(cfg)
        sensors = [{**v, **geo[v["pos"]]} for v in self.rf.views(now)]
        ox, oy = vehicle.to_plot(0.0, 0.0, cfg)
        return {"state": self.rf.status.state, "sensors": sensors,
                "vehicle": {"x": ox, "y": oy, "front_deg": cfg["angle_offset_deg"], "length": cfg["vehicle_length_m"],
                            "width": cfg["vehicle_width_m"], "calibrated": cfg["geometry_calibrated"]},
                "monitor": self.alert.get("monitor"),
                "thresholds": {k: cfg[f"prox_{k}_m"] for k in ("danger", "warning", "caution")}}

    def unplugged_for(self, now=None) -> float | None:
        """Seconds since a lidar that WAS scanning stopped delivering, or None (never connected / replay / fine)."""
        cfg, ls = self.cfg.get(), self.driver.status
        if cfg["source"] != "serial" or ls.last_scan_t is None:
            return None
        gone = (now or time.time()) - ls.last_scan_t
        return gone if gone > 3 else None

    def _unplug_watch(self):
        while True:
            time.sleep(1)
            cfg = self.cfg.get()
            gone = self.unplugged_for()
            if not cfg["exit_when_unplugged"] or gone is None:
                self.closing_in = None
                continue
            self.closing_in = max(0.0, cfg["unplug_grace_s"] - gone)
            if self.closing_in == 0:
                log.warning("lidar unplugged for %.0f s: shutting down", gone)
                self.shutdown()

    def shutdown(self):
        """Close files cleanly, then stop the server the same way Ctrl+C does."""
        import signal
        if self._rec_file:
            self._rec_file.close()
            self._rec_file = None
        self.driver.stop()
        if self.on_exit:
            self.on_exit()
        else:
            signal.raise_signal(signal.SIGINT)

    # ------------------------------------------------------------ dataset
    FOLLOW_EVERY_S = 0.5   # capture interval while following a labeled object
    FOLLOW_MAX = 40        # samples per object; more frames of one object add little

    def label_track(self, track_id: int, label: str, notes="", follow=False):
        frame = self.frame or {}
        if frame.get("source") == "simulation":
            raise ValueError("Simulated objects can't be labeled: only real objects go into the dataset")
        d = next((t for t in frame.get("tracks", []) if t["id"] == track_id), None)
        tr = next((t for t in self.tracker.tracks if t.id == track_id), None)
        if d is None or tr is None:
            raise KeyError("Track is no longer visible")
        # one label per physical object: relabel anything already captured for it
        self.db.relabel_object(self.started, track_id, label)
        sid = self._save_sample(tr, d, label, notes, frame.get("source", ""))
        if follow:
            self.follow[track_id] = [label, frame.get("t", time.time()), 1]   # same clock as scan.t
        return sid

    def stop_follow(self, track_id: int):
        self.follow.pop(track_id, None)

    def _save_sample(self, tr, d, label, notes, source):
        feats = dict(zip(ML_FEATURES, feature_vector(d)))
        return self.db.add_sample(label, tr.id, feats, tr.cluster["points"], notes, source, self.started)

    def _follow_capture(self, tr, d, t, frame_source):
        f = self.follow.get(tr.id)
        if not f or tr.missed or t - f[1] < self.FOLLOW_EVERY_S:
            return
        f[1] = t
        f[2] += 1
        self._save_sample(tr, d, f[0], "follow", frame_source)
        if f[2] >= self.FOLLOW_MAX:
            self.follow.pop(tr.id, None)

    def status(self) -> dict:
        from serial.tools import list_ports
        cfg = self.cfg.get()
        ls = self.driver.status
        ports = [{"device": p.device, "description": p.description, "vid": p.vid, "pid": p.pid}
                 for p in list_ports.comports()]
        pico = [p for p in ports if p["vid"] == 0x2E8A]
        return {
            "uptime_s": time.time() - self.started,
            "lidar": {**ls.__dict__, "stale": ls.last_scan_t is None or time.time() - ls.last_scan_t > 2},
            "pico": {"detected": bool(pico), "port": pico[0]["device"] if pico else None,
                     "role": "optional (direct-USB architecture: not used for lidar data)"},
            "replay": self.replay.state(),
            "ports": ports,
            "processing": {"ms": self.proc_ms, "dropped_scans": self.dropped, "queue": self.q.qsize()},
            "counts": self.counts,
            "background": {"ready": self.bg.ready, "progress": self.bg.progress, "enabled": cfg["background_enabled"],
                           "absorb_s": cfg["background_absorb_s"]},
            "ml": {"available": self.ml.available, "meta": self.ml.meta, "active": cfg["classifier"] == "ml"},
            "twin_dataset": twin.dataset_meta(),
            "ml_job": self.ml_job,
            "rangefinders": {**self.rf.status.__dict__, "sensors": self.rf.views()},
            "alert": self.alert,
            "alert_prefs": {"sound": cfg["alert_sound"], "volume": cfg["alert_volume"], "light": cfg["alert_light"]},
            "nn": {"installed": self.nn.installed, "available": self.nn.available, "meta": self.nn.meta,
                   "active": cfg["classifier"] == "nn"},
            "source": cfg["source"], "recording": cfg["record"],
            "dataset": self.db.dataset_summary(),
            "closing_in": self.closing_in,
            "following": [{"track_id": k, "label": v[0], "samples": v[2]} for k, v in self.follow.items()],
        }


class ReplayThread(threading.Thread):
    """Plays back a recorded file of genuine scans with transport controls, for labeling from recordings.
    Scans keep their RECORDED timestamps, so tracking speeds stay correct in slow motion and the clock
    shown in the UI can be matched against a video filmed at the same time. Marked REPLAY in the UI."""

    SPEEDS = (0.1, 0.25, 0.5, 1.0, 2.0)

    def __init__(self, engine: Engine):
        super().__init__(daemon=True, name="replay")
        self.engine = engine
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.file, self.lines, self.pos, self.message = None, [], 0, "idle"
        self.paused, self.speed, self.step_n = False, 1.0, 0
        self.rec_t0 = self.rec_t = None

    # ---- controls (called from API threads)
    def play(self):
        with self.lock:
            if self.lines and self.pos >= len(self.lines):
                self._seek(0)
            self.paused = False
        self.wake.set()

    def pause(self):
        self.paused = True

    def step(self, n=1):
        with self.lock:
            self.paused, self.step_n = True, max(1, int(n))
        self.wake.set()

    def seek(self, pos):
        with self.lock:
            self._seek(pos)
        self.wake.set()

    def set_speed(self, v):
        if float(v) not in self.SPEEDS:
            raise ValueError(f"speed must be one of {self.SPEEDS}")
        self.speed = float(v)

    def _seek(self, pos):
        self.pos = max(0, min(int(pos), max(len(self.lines) - 1, 0)))
        self.engine.reset_tracking = True   # tracks from the old position are meaningless after a jump

    def state(self):
        return {"file": self.file, "pos": self.pos, "total": len(self.lines), "message": self.message,
                "paused": self.paused, "speed": self.speed, "rec_t": self.rec_t, "rec_t0": self.rec_t0}

    # ---- playback loop
    def _load(self, name):
        path = DATA / "recordings" / Path(name).name   # no path traversal
        if not path.exists():
            self.file, self.lines, self.message = None, [], f"File not found: {path.name}"
            return
        self.lines = path.read_text(encoding="utf-8").splitlines()
        self.file, self.pos, self.paused, self.message = name, 0, False, "playing"
        self.rec_t0 = json.loads(self.lines[0])["t"] if self.lines else None
        self.engine.reset_tracking = True

    def run(self):
        prev_t = None
        while True:
            cfg = self.engine.cfg.get()
            if cfg["source"] != "replay" or not cfg["replay_file"]:
                self.message = "idle"
                self.wake.wait(1.0); self.wake.clear()
                continue
            if cfg["replay_file"] != self.file:
                self._load(cfg["replay_file"])
                prev_t = None
                continue
            with self.lock:
                at_end = self.pos >= len(self.lines)
                go = (not self.paused or self.step_n > 0) and not at_end
                if go:
                    scan = Scan.from_json(json.loads(self.lines[self.pos]))
                    self.pos += 1
                    self.step_n = max(0, self.step_n - 1)
            if at_end:
                self.message, self.paused = "finished", True
            if not go:
                if not at_end:
                    self.message = "paused"
                prev_t = None
                self.wake.wait(0.5); self.wake.clear()
                continue
            self.message = "playing" if not self.paused else "paused"
            if prev_t is not None and not self.paused:
                self.wake.wait(max(0.0, min(scan.t - prev_t, 1.0)) / self.speed); self.wake.clear()
            prev_t = scan.t
            self.rec_t = scan.t
            self.engine.on_scan(scan)

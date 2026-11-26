"""Digital twin of the roadside setup: simulated T-mini Plus scans of 3D object models, used to train a
STARTER classifier before real labeled data exists.

Honesty rules:
  * Simulated data never enters the live view, the real dataset, or Data History.
  * A twin model's method is "ml_synthetic"; its cross-validation score is measured on SIMULATED objects
    and says nothing about real-world accuracy. If real labeled samples exist, the model is also scored on
    them ("sim-to-real"), which is the number that matters.

Sensor model (T-mini Plus manual): ~0.54 deg resolution at 6 Hz, range 0.05-12 m at 80 % reflectivity and
0.05-4 m at 10 % (max range interpolated by reflectivity), typical error ~20 mm (modelled as noise).
Object models: simplified boxes and wheel rings with typical published dimensions; they are approximations.
"""
from __future__ import annotations

import json
import math
import threading
import time

import numpy as np

from .config import DATA, DEFAULTS
from .processing import Background, process
from .tracking import Tracker

MODEL_FILE = DATA / "models" / "twin.joblib"
META_FILE = DATA / "models" / "twin.json"
CLASSES = ["car", "motorcycle", "bicycle", "pedestrian"]
SCAN_HZ = 6.0
STEP_DEG = 0.54
NOISE_M = 0.015


# ---------------------------------------------------------------- 3D object models (metres; x = travel, z = up)
def box(x0, x1, y0, y1, z0, z1):
    return {"type": "box", "x": [x0, x1], "y": [y0, y1], "z": [z0, z1]}


def wheel(xc, yc, zc, r, width, rim):
    """Wheel as a ring around a y-axis: `rim` is the radial thickness that blocks the beam (r = solid disc)."""
    return {"type": "wheel", "c": [xc, yc, zc], "r": r, "w": width, "rim": rim}


def model(kind: str, rng=None, phase: float = 0.0) -> list[dict]:
    """3D primitives for one object. `rng` varies the size; `phase` animates walking/pedalling legs."""
    s = 1.0 if rng is None else rng.uniform(0.9, 1.1)
    if kind == "car":
        L, W = 4.4 * s, 1.8 * s
        return [box(-L / 2, L / 2, -W / 2, W / 2, 0.30, 1.00),
                box(-L / 2 + 1.0, L / 2 - 1.2, -W / 2 + 0.1, W / 2 - 0.1, 1.00, 1.45),
                *[wheel(x, y, 0.32, 0.32, 0.2, 0.32) for x in (-1.4 * s, 1.4 * s) for y in (-W / 2 + 0.1, W / 2 - 0.1)]]
    if kind == "motorcycle":
        return [wheel(-0.70 * s, 0, 0.31, 0.31, 0.14, 0.12), wheel(0.70 * s, 0, 0.31, 0.31, 0.14, 0.12),
                box(-0.30, 0.30, -0.20, 0.20, 0.30, 0.65),            # engine
                box(-0.60, 0.40, -0.17, 0.17, 0.65, 0.85),            # tank + seat
                box(-0.05, 0.25, 0.15, 0.27, 0.40, 0.85), box(-0.05, 0.25, -0.27, -0.15, 0.40, 0.85),   # rider legs
                box(-0.35, -0.05, -0.20, 0.20, 0.85, 1.45)]           # rider torso
    if kind == "bicycle":
        k = 0.12 * math.sin(phase)                                    # pedalling
        return [wheel(-0.53 * s, 0, 0.34, 0.34, 0.04, 0.03), wheel(0.53 * s, 0, 0.34, 0.34, 0.04, 0.03),
                box(-0.02, 0.02, -0.02, 0.02, 0.25, 0.80),            # seat tube
                box(0.40, 0.44, -0.02, 0.02, 0.45, 1.00),             # head tube / fork
                box(-0.05 + k, 0.07 + k, 0.08, 0.18, 0.30, 0.85), box(-0.05 - k, 0.07 - k, -0.18, -0.08, 0.30, 0.85),
                box(-0.20, 0.10, -0.20, 0.20, 0.85, 1.50)]            # rider torso
    if kind == "pedestrian":
        k = 0.15 * math.sin(phase)                                    # stride
        return [box(-0.06 + k, 0.06 + k, 0.04, 0.16, 0.0, 0.85), box(-0.06 - k, 0.06 - k, -0.16, -0.04, 0.0, 0.85),
                box(-0.12, 0.12, -0.21, 0.21, 0.85, 1.45)]
    raise ValueError(kind)


def slice_at(prims: list[dict], h: float) -> list[tuple[float, float, float, float]]:
    """Horizontal cross-section at height h: rectangles (x0, x1, y0, y1) in the object frame."""
    out = []
    for p in prims:
        if p["type"] == "box":
            if p["z"][0] <= h <= p["z"][1]:
                out.append((*p["x"], *p["y"]))
        else:
            xc, yc, zc = p["c"]
            dz = abs(h - zc)
            if dz >= p["r"]:
                continue
            a = math.sqrt(p["r"] ** 2 - dz ** 2)
            inner = p["r"] - p["rim"]
            b = math.sqrt(inner ** 2 - dz ** 2) if dz < inner else 0.0
            y0, y1 = yc - p["w"] / 2, yc + p["w"] / 2
            if b == 0.0:
                out.append((xc - a, xc + a, y0, y1))
            else:
                out += [(xc - a, xc - b, y0, y1), (xc + b, xc + a, y0, y1)]
    return out


# ---------------------------------------------------------------- ray casting
def _edges(rects, x, y, heading):
    c, s = math.cos(heading), math.sin(heading)
    segs = []
    for x0, x1, y0, y1 in rects:
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        w = [(x + c * px - s * py, y + s * px + c * py) for px, py in pts]
        segs += [(w[i], w[(i + 1) % 4]) for i in range(4)]
    return segs


def raycast(segments, reflect, rng, n_rays):
    """Nearest hit per ray from the origin. segments: [((x,y),(x,y))]; reflect: per-segment max range (m)."""
    th = np.radians(np.arange(n_rays) * 360.0 / n_rays)
    D = np.column_stack([np.cos(th), np.sin(th)])
    dist = np.full(n_rays, np.inf)
    if segments:
        P = np.array([a for a, _ in segments]); E = np.array([b for _, b in segments]) - P
        cross = lambda u, v: u[..., 0] * v[..., 1] - u[..., 1] * v[..., 0]
        den = cross(D[:, None, :], E[None, :, :])
        with np.errstate(divide="ignore", invalid="ignore"):
            t = cross(P[None, :, :], E[None, :, :]) / den
            u = cross(P[None, :, :], D[:, None, :]) / den
        ok = (np.abs(den) > 1e-12) & (t > 0) & (u >= 0) & (u <= 1)
        t = np.where(ok & (t <= np.asarray(reflect)[None, :]), t, np.inf)
        dist = t.min(axis=1)
    hit = np.isfinite(dist) & (dist >= 0.05)
    dist = np.where(hit, dist + rng.normal(0, NOISE_M, n_rays), 0.0)
    dist[rng.random(n_rays) < 0.01] = 0.0                                   # occasional dropouts
    # plot angle theta = -sensor_angle (mirror=True, offset 0) -> sensor angle = -theta
    angles = (-np.degrees(th)) % 360.0
    return angles, np.clip(dist * 1000, 0, None).astype(np.float32)


def max_range(reflectivity):
    """Manual: 12 m at 80 %, 4 m at 10 %; linear in between (approximation)."""
    return 4.0 + (12.0 - 4.0) * (np.clip(reflectivity, 0.1, 0.8) - 0.1) / 0.7


SPEEDS = {"car": (3, 14), "motorcycle": (3, 15), "bicycle": (2, 7), "pedestrian": (0.8, 1.8)}


class _Scan:   # minimal stand-in for ydlidar.Scan
    def __init__(self, t, a, d):
        n = len(a)
        self.t, self.angles, self.dist_mm = t, a, d
        self.intensity, self.flag = np.full(n, 100, np.uint8), np.zeros(n, np.uint8)


def static_scene(rng):
    """Roadside clutter that the adaptive background must learn: a wall behind the sensor and two poles."""
    poles = [(x - 0.06, x + 0.06, -1.2, -1.08) for x in (rng.uniform(-6, -2), rng.uniform(2, 6))]
    return [(-12.0, 12.0, -1.6, -1.4)] + poles


def simulate_pass(kind, height, rng, cfg, warm_bg):
    """One object crossing the scene; returns [(features dict, points)] sampled every 0.5 s from its track."""
    from .classify import feature_vector
    n_rays = int(round(360 / STEP_DEG))
    lane = rng.uniform(1.5, 7.0) * rng.choice([-1, 1]) if kind != "pedestrian" else rng.uniform(1.0, 6.0) * rng.choice([-1, 1])
    if lane < 0 and abs(lane) < 2.0:
        lane = -2.0 - abs(lane)                                    # stay in front of the wall at y=-1.5
    direction = rng.choice([-1, 1])
    speed = rng.uniform(*SPEEDS[kind])
    refl = rng.uniform(0.1, 0.8)
    obj_seed = int(rng.integers(1 << 30))                         # same size for every frame of this object
    static = static_scene(np.random.default_rng(0))
    bg = Background()
    bg.snaps, bg.ranges, bg.last_t = list(warm_bg.snaps), warm_bg.ranges.copy(), None
    tracker, samples = Tracker(), []
    x0 = -9.0 * direction
    t, phase_rate = 1000.0, (2 * math.pi * speed / 1.4 if kind in ("pedestrian", "bicycle") else 0.0)
    while abs(x0 + direction * speed * (t - 1000.0)) <= 9.0:
        x = x0 + direction * speed * (t - 1000.0)
        prims = model(kind, rng=np.random.default_rng(obj_seed), phase=phase_rate * (t - 1000.0))
        segs = _edges(slice_at(prims, height), x, lane, 0.0 if direction > 0 else math.pi)
        segs_static = _edges(static, 0, 0, 0)
        reflect = [max_range(refl)] * len(segs) + [12.0] * len(segs_static)
        a, d = raycast(segs + segs_static, reflect, rng, n_rays)
        _, clusters = process(_Scan(t, a, d), cfg, bg)
        tracks = tracker.step(clusters, t, cfg)
        # every confirmed track this scan is (part of) the simulated object: an object split into pieces
        # by the pipeline is seen the same way live, so each piece is a sample of this class
        for tr in tracks:
            if tr.missed == 0:
                samples.append((feature_vector(Tracker.describe(tr, cfg)), tr.cluster["points"]))
        t += 1.0 / SCAN_HZ
        if len(samples) >= 40:
            break
    return samples


def warm_background(cfg):
    rng = np.random.default_rng(0)
    static = _edges(static_scene(np.random.default_rng(0)), 0, 0, 0)
    bg = Background()
    for i in range(6):
        a, d = raycast(static, [12.0] * len(static), rng, int(round(360 / STEP_DEG)))
        process(_Scan(i * 2.0, a, d), cfg, bg)
    return bg


def generate(height: float, per_class: int, seed=0, progress=None):
    """Simulated dataset: X (features), y (labels), groups (object ids)."""
    cfg = dict(DEFAULTS, angle_offset_deg=0.0, mirror=True)
    rng = np.random.default_rng(seed)
    bg = warm_background(cfg)
    X, y, g = [], [], []
    total = per_class * len(CLASSES)
    for ci, kind in enumerate(CLASSES):
        for i in range(per_class):
            for fv, _pts in simulate_pass(kind, height, rng, cfg, bg):
                X.append(fv); y.append(kind); g.append(f"{kind}-{i}")
            if progress:
                progress((ci * per_class + i + 1) / total)
    return X, y, g


# ---------------------------------------------------------------- model
class TwinModel:
    """Random Forest trained only on digital-twin data. Intensity is excluded (the twin does not model it)."""

    def __init__(self):
        self.model, self.meta, self.cols = None, None, None
        self.job = {"running": False, "progress": 0.0, "message": "idle"}
        self.load()

    @property
    def available(self):
        return self.model is not None

    def load(self):
        if MODEL_FILE.exists() and META_FILE.exists():
            try:
                import joblib
                self.model, self.meta = joblib.load(MODEL_FILE), json.loads(META_FILE.read_text())
                self.cols = self.meta["feature_index"]
            except Exception:
                self.model = self.meta = None

    def predict(self, fv, min_conf):
        p = self.model.predict_proba([[fv[i] for i in self.cols]])[0]
        k = int(np.argmax(p))
        return (self.model.classes_[k] if p[k] >= min_conf else "unknown"), float(p[k]), "ml_synthetic"

    def start_training(self, height, per_class, real_samples):
        if self.job["running"]:
            raise ValueError("Already training")
        self.job = {"running": True, "progress": 0.0, "message": "simulating"}
        threading.Thread(target=self._train, args=(height, per_class, real_samples), daemon=True).start()

    def _train(self, height, per_class, real_samples):
        try:
            self.meta = self.train(height, per_class, real_samples,
                                   progress=lambda f: self.job.update(progress=0.9 * f))
            self.job = {"running": False, "progress": 1.0, "message": "done"}
        except Exception as e:
            self.job = {"running": False, "progress": 0.0, "message": f"failed: {e}"}

    def train(self, height, per_class, real_samples=(), progress=None):
        import joblib
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
        from sklearn.model_selection import StratifiedGroupKFold, cross_val_predict
        from .classify import ML_FEATURES

        cols = [i for i, n in enumerate(ML_FEATURES) if n != "mean_intensity"]
        X, y, g = generate(height, per_class, progress=progress)
        X, y, g = np.asarray(X)[:, cols], np.asarray(y), np.asarray(g)
        clf = RandomForestClassifier(n_estimators=200, class_weight="balanced", random_state=0)
        pred = cross_val_predict(clf, X, y, groups=g, cv=StratifiedGroupKFold(5, shuffle=True, random_state=0))
        clf.fit(X, y)
        cls = clf.classes_.tolist()
        pr, rc, f1, _ = precision_recall_fscore_support(y, pred, labels=cls, zero_division=0)
        meta = {
            "version": time.strftime("twin-%Y%m%d-%H%M%S"), "mount_height_m": height,
            "algorithm": "RandomForestClassifier trained on digital-twin scans",
            "classes": cls, "features": [ML_FEATURES[i] for i in cols], "feature_index": cols,
            "samples": {c: int((y == c).sum()) for c in cls},
            "objects": {c: len(set(g[y == c])) for c in cls},   # objects the simulated sensor actually saw
            "cv_folds": 5, "cv_grouped_by": "simulated object", "cv_accuracy": float(accuracy_score(y, pred)),
            "confusion_matrix": confusion_matrix(y, pred, labels=cls).tolist(),
            "per_class": {c: {"precision": float(pr[i]), "recall": float(rc[i]), "f1": float(f1[i]),
                              "samples": int((y == c).sum())} for i, c in enumerate(cls)},
            "sim_to_real": None, "validated": False, "trained_at": time.time(),
            "note": "Accuracy measured on SIMULATED objects only. Real-world accuracy is unknown until checked on real labels.",
        }
        real = [(s["fv"], s["label"]) for s in real_samples if s["label"] in cls]
        if real:
            Xr = np.asarray([[fv[i] for i in cols] for fv, _ in real]); yr = np.asarray([lab for _, lab in real])
            pr_ = clf.predict(Xr)
            meta["sim_to_real"] = {"accuracy": float(accuracy_score(yr, pr_)), "samples": len(yr),
                                   "confusion_matrix": confusion_matrix(yr, pr_, labels=cls).tolist()}
        joblib.dump(clf, MODEL_FILE)
        META_FILE.write_text(json.dumps(meta, indent=2))
        self.model, self.cols = clf, cols
        return meta


def preview(height: float) -> dict:
    """3D primitives + cross-sections for the UI's 3D view of each class."""
    return {k: {"prims": model(k), "slice": slice_at(model(k), height)} for k in CLASSES}


# ---------------------------------------------------------------- live simulation source
class SimulationThread(threading.Thread):
    """Live pretend traffic for using the app without the LiDAR (source = "simulation").
    Emits twin scans at the T-mini Plus rate. The engine marks every frame SIMULATION and never records,
    logs or labels it."""

    def __init__(self, engine):
        super().__init__(daemon=True, name="simulation")
        self.engine = engine
        self.rng = np.random.default_rng()
        self.objects: list[dict] = []
        self.next_spawn = 0.0
        self.static = _edges(static_scene(np.random.default_rng(0)), 0, 0, 0)

    def _spawn(self, now):
        kind = str(self.rng.choice(CLASSES, p=[0.35, 0.3, 0.1, 0.25]))   # roughly what passes a Leyte roadside
        side = self.rng.choice([-1, 1])
        lane = side * (self.rng.uniform(1.0, 3.0) if kind == "pedestrian" else self.rng.uniform(2.0, 6.0))
        if lane < 0 and abs(lane) < 2.0:
            lane = -2.0 - abs(lane)                                  # stay in front of the wall behind the sensor
        direction = self.rng.choice([-1, 1])
        self.objects.append({"kind": kind, "lane": lane, "dir": direction, "x": -10.0 * direction,
                             "speed": self.rng.uniform(*SPEEDS[kind]) * (0.6 if kind != "pedestrian" else 1.0),
                             "refl": self.rng.uniform(0.2, 0.8), "seed": int(self.rng.integers(1 << 30)), "t0": now})
        self.next_spawn = now + self.rng.uniform(1.5, 5.0)

    def run(self):
        n_rays = int(round(360 / STEP_DEG))
        while True:
            if self.engine.cfg.get()["source"] != "simulation":
                self.objects = []
                time.sleep(0.5)
                continue
            now = time.time()
            if now >= self.next_spawn and len(self.objects) < 4:
                self._spawn(now)
            segs, reflect = [], []
            for o in self.objects:
                o["x"] += o["dir"] * o["speed"] / SCAN_HZ
                prims = model(o["kind"], rng=np.random.default_rng(o["seed"]),
                              phase=2 * math.pi * o["speed"] / 1.4 * (now - o["t0"]))
                e = _edges(slice_at(prims, self.engine.cfg.get()["mount_height_m"]), o["x"], o["lane"],
                           0.0 if o["dir"] > 0 else math.pi)
                segs += e
                reflect += [max_range(o["refl"])] * len(e)
            self.objects = [o for o in self.objects if abs(o["x"]) <= 11.0]
            a, d = raycast(segs + self.static, reflect + [12.0] * len(self.static), self.rng, n_rays)
            scan = _Scan(now, a, d)
            scan.freq_hz = SCAN_HZ
            self.engine.on_scan(scan)
            time.sleep(max(0.0, 1.0 / SCAN_HZ - (time.time() - now)))

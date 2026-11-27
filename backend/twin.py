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
from . import dataset
from .processing import Background, process
from .tracking import Tracker

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


def pick_size(kind: str, rng):
    """(length, width) of a random workbook row of this category (backend/dataset.py), or None -> built-in size."""
    rows = dataset.sizes().get(kind)
    return None if not rows else rows[int(rng.integers(len(rows)))][1:]


def model(kind: str, rng=None, phase: float = 0.0, size=None) -> list[dict]:
    """3D primitives for one object. `rng` varies the size; `phase` animates walking/pedalling legs;
    `size` = (length, width) from the workbook replaces the built-in typical size."""
    s = 1.0 if rng is None else rng.uniform(0.9, 1.1)
    if size and kind in ("motorcycle", "bicycle"):
        s *= size[0] / (2.0 if kind == "motorcycle" else 1.8)       # built-in models are ~2.0 / ~1.8 m long
    if kind == "car":
        L, W = (size or (4.4, 1.8))[0] * s, (size or (4.4, 1.8))[1] * s
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
        sx, sy = (size[0] / 0.24, size[1] / 0.42) if size else (1.0, 1.0)   # built-in person: 0.24 deep x 0.42 wide
        return [box(sx * (-0.06 + k), sx * (0.06 + k), sy * 0.04, sy * 0.16, 0.0, 0.85),
                box(sx * (-0.06 - k), sx * (0.06 - k), sy * -0.16, sy * -0.04, 0.0, 0.85),
                box(sx * -0.12, sx * 0.12, sy * -0.21, sy * 0.21, 0.85, 1.45)]
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


SPEEDS = {"car": (3, 14), "motorcycle": (3, 15), "bicycle": (2, 7), "pedestrian": (0.3, 1.8)}   # slow walkers too; standing people are added in simulate_pass


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
    standing = kind == "pedestrian" and rng.random() < 0.25       # a person waiting by the road reads ~0 m/s
    if standing:
        speed = 0.0
    refl = rng.uniform(0.1, 0.8)
    obj_seed = int(rng.integers(1 << 30))                         # same size for every frame of this object
    size = pick_size(kind, np.random.default_rng(obj_seed))
    static = static_scene(np.random.default_rng(0))
    bg = Background()
    bg.snaps, bg.ranges, bg.last_t = list(warm_bg.snaps), warm_bg.ranges.copy(), None
    tracker, samples = Tracker(), []
    x0 = rng.uniform(-6, 6) if kind == "pedestrian" else -9.0 * direction   # people are met mid-scene
    t, phase_rate = 1000.0, (2 * math.pi * speed / 1.4 if kind in ("pedestrian", "bicycle") else 0.0)
    while abs(x0 + direction * speed * (t - 1000.0)) <= 9.0 and t - 1000.0 < 20.0:
        x = x0 + direction * speed * (t - 1000.0)
        prims = model(kind, rng=np.random.default_rng(obj_seed), phase=phase_rate * (t - 1000.0), size=size)
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
    """Simulated dataset: X (features), y (labels), groups (object ids), points (each sample's 2D points)."""
    cfg = dict(DEFAULTS, angle_offset_deg=0.0, mirror=True)
    rng = np.random.default_rng(seed)
    bg = warm_background(cfg)
    X, y, g, P = [], [], [], []
    total = per_class * len(CLASSES)
    for ci, kind in enumerate(CLASSES):
        for i in range(per_class):
            for fv, pts in simulate_pass(kind, height, rng, cfg, bg):
                X.append(fv); y.append(kind); g.append(f"twin:{kind}-{i}"); P.append(pts)
            if progress:
                progress((ci * per_class + i + 1) / total)
    return X, y, g, P


# ---------------------------------------------------------------- the saved synthetic dataset
DATASET_FILE = DATA / "datasets" / "twin_dataset.joblib"
GENERATOR = "bantay-twin-2"     # bump when the simulator changes, so old datasets are regenerated


def build_dataset(height: float, per_class: int, progress=None, seed=0) -> dict:
    """Generate and SAVE the synthetic partition (kept apart from the researcher's real samples)."""
    import joblib
    X, y, g, P = generate(height, per_class, seed=seed, progress=progress)
    y_arr, g_arr = np.asarray(y), np.asarray(g)
    meta = {"version": time.strftime("twinset-%Y%m%d-%H%M%S"), "generator": GENERATOR, "seed": seed,
            "mount_height_m": height, "objects_requested_per_class": per_class, "created": time.time(),
            "classes": CLASSES, "size_source": {k: [r[0] for r in v] for k, v in dataset.sizes().items()},
            "samples": {c: int((y_arr == c).sum()) for c in CLASSES},
            "objects": {c: len(set(g_arr[y_arr == c])) for c in CLASSES},
            "note": "SIMULATED scans from the digital twin. Never mixed into the real dataset."}
    DATASET_FILE.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"X": X, "y": y, "groups": g, "points": P, "meta": meta}, DATASET_FILE)
    DATASET_FILE.with_suffix(".json").write_text(json.dumps(meta, indent=2))
    return meta


def dataset_meta() -> dict | None:
    """Metadata of the saved synthetic partition (cheap: does not load the samples)."""
    f = DATASET_FILE.with_suffix(".json")
    try:
        m = json.loads(f.read_text())
        return m if m.get("generator") == GENERATOR else None
    except (OSError, ValueError):
        return None


def load_dataset() -> dict | None:
    import joblib
    if not DATASET_FILE.exists():
        return None
    try:
        d = joblib.load(DATASET_FILE)
        return d if d.get("meta", {}).get("generator") == GENERATOR else None
    except Exception:
        return None


def preview(height: float) -> dict:
    """3D primitives + cross-sections for the UI's 3D view of each class."""
    return {k: {"prims": model(k), "slice": slice_at(model(k), height)} for k in CLASSES}


def ray_hit(ox, oy, deg, segments, reflect):
    """Distance along one ray from (ox, oy) to the nearest segment, or None (single-beam range finder)."""
    if not segments:
        return None
    d = np.array([math.cos(math.radians(deg)), math.sin(math.radians(deg))])
    P = np.array([a for a, _ in segments]) - np.array([ox, oy]); E = np.array([b for _, b in segments]) - np.array([a for a, _ in segments])
    den = d[0] * E[:, 1] - d[1] * E[:, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (P[:, 0] * E[:, 1] - P[:, 1] * E[:, 0]) / den
        u = (P[:, 0] * d[1] - P[:, 1] * d[0]) / den
    ok = (np.abs(den) > 1e-12) & (t > 0) & (u >= 0) & (u <= 1) & (t <= np.asarray(reflect))
    return float(t[ok].min()) if ok.any() else None


TFL_RANGE = (0.2, 8.0)     # Benewake TF-Luna product page: 0.2 ~ 8 m


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
        self.objects[-1]["size"] = pick_size(kind, self.rng)
        self.next_spawn = now + self.rng.uniform(1.5, 5.0)

    def _simulate_tfluna(self, segs, reflect, now):
        """The three TF-Luna beams through the same simulated scene, from their configured mounts."""
        from .vehicle import sensor_geometry
        cfg = self.engine.cfg.get()
        off = cfg["angle_offset_deg"]
        out = {}
        for pos, g in sensor_geometry(cfg).items():
            # plot frame -> this simulation's frame (the engine rotates scans by the mounting offset)
            a = math.radians(-off)
            sx, sy = g["x"] * math.cos(a) - g["y"] * math.sin(a), g["x"] * math.sin(a) + g["y"] * math.cos(a)
            hit = ray_hit(sx, sy, g["angle"] - off, segs, reflect)
            if hit is not None:
                hit += float(self.rng.normal(0, 0.02))
            out[pos] = hit if hit is not None and TFL_RANGE[0] <= hit <= TFL_RANGE[1] else None
        self.engine.rf.inject_simulated(out, now)

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
                              phase=2 * math.pi * o["speed"] / 1.4 * (now - o["t0"]), size=o["size"])
                e = _edges(slice_at(prims, self.engine.cfg.get()["mount_height_m"]), o["x"], o["lane"],
                           0.0 if o["dir"] > 0 else math.pi)
                segs += e
                reflect += [max_range(o["refl"])] * len(e)
            self.objects = [o for o in self.objects if abs(o["x"]) <= 11.0]
            all_segs, all_refl = segs + self.static, reflect + [12.0] * len(self.static)
            a, d = raycast(all_segs, all_refl, self.rng, n_rays)
            self._simulate_tfluna(all_segs, all_refl, now)
            scan = _Scan(now, a, d)
            scan.freq_hz = SCAN_HZ
            self.engine.on_scan(scan)
            time.sleep(max(0.0, 1.0 / SCAN_HZ - (time.time() - now)))

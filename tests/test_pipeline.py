"""Pipeline tests on geometric test fixtures (synthetic, test-only; never shown in the application)."""
import numpy as np
import pytest

from backend.classify import MLClassifier, rule_classify
from backend.config import DEFAULTS
from backend.processing import Background, process, to_xy
from backend.tracking import Tracker
from backend.ydlidar import Scan

CFG = dict(DEFAULTS)
NF = 8   # len(ML_FEATURES)


def warm(bg, t0=0.0):
    """Feed an empty scene until the adaptive background is ready (5 snapshots, 2 s apart)."""
    for i in range(5):
        process(ring_scan(t0 + i * 2.0), CFG, bg)
    assert bg.ready
    return t0 + 10.0


def ring_scan(t, objects=(), wall=6.0):
    """Room of radius `wall` plus flat objects: (x, y, half_length) segments along x."""
    a = np.arange(0, 360, 0.54)
    r = np.full(len(a), wall)
    for ox, oy, half in objects:
        # sensor angle is clockwise; with mirror=True, plot angle = -a
        for k, ang in enumerate(a):
            th = np.radians(-ang)
            d, v = np.array([np.cos(th), np.sin(th)]), None
            if abs(d[1]) > 1e-9:
                s = oy / d[1]
                if s > 0 and abs(s * d[0] - ox) <= half:
                    v = s
            if v is not None and v < r[k]:
                r[k] = v
    return Scan(t, a, (r * 1000).astype(np.float32), np.full(len(a), 100, np.uint8), np.zeros(len(a), np.uint8))


def test_to_xy_convention():
    x, y = to_xy(np.array([0.0, 90.0]), np.array([1.0, 1.0]), CFG)
    assert np.allclose([x[0], y[0]], [1, 0])
    assert np.allclose([x[1], y[1]], [0, -1])   # clockwise sensor 90 deg -> -y when mirrored


def test_invalid_and_flagged_points_removed():
    s = ring_scan(0)
    s.dist_mm[:10] = 0          # invalid distance
    s.flag[10:20] = 3           # ambient-light interference
    frame, _ = process(s, CFG, Background())
    assert frame["n_valid"] == len(s.angles) - 20


def test_background_then_object_cluster():
    bg = Background()
    t = warm(bg)
    _, clusters = process(ring_scan(t, [(0.0, 3.0, 1.5)]), CFG, bg)
    assert len(clusters) == 1
    c = clusters[0]
    assert abs(c["cy"] - 3.0) < 0.05 and 2.5 < c["features"]["length_m"] < 3.1


def test_tracker_velocity_and_rules():
    bg = Background()
    t0 = warm(bg)
    tr = Tracker()
    out = []
    for i in range(15):
        t = t0 + i * 0.1
        _, cl = process(ring_scan(t, [(-2 + 0.2 * i, 3.0, 1.5)]), CFG, bg)   # 2 m/s along +x
        out = tr.step(cl, t, CFG)
    d = Tracker.describe(out[0], CFG)
    assert abs(d["speed"] - 2.0) < 0.4 and abs(d["heading"]) < 20
    assert rule_classify(d)[0] == "car"


def test_rules_refuse_two_wheeler_split():
    d = {"max_length": 1.8, "features": {"width_m": 0.2}, "speed": 5.0}
    assert rule_classify(d)[0] == "unknown"
    d = {"max_length": 0.4, "features": {"width_m": 0.2}, "speed": None}
    assert rule_classify(d)[0] == "unknown"   # no measured speed -> no pedestrian claim


def test_training_refuses_without_data(tmp_path, monkeypatch):
    import backend.classify as c
    monkeypatch.setattr(c, "MODEL_FILE", tmp_path / "m.joblib")
    monkeypatch.setattr(c, "META_FILE", tmp_path / "m.json")
    ml = MLClassifier()
    with pytest.raises(ValueError):
        ml.train([[0] * NF] * 3, ["car"] * 3, ["a", "b", "c"])
    # 2 classes but each from only 1 object: grouped CV is impossible, so training must refuse
    with pytest.raises(ValueError):
        ml.train([[0] * NF] * 10 + [[1] * NF] * 10, ["car"] * 10 + ["pedestrian"] * 10, ["o1"] * 10 + ["o2"] * 10)


def test_training_grouped_cv(tmp_path, monkeypatch):
    import backend.classify as c
    monkeypatch.setattr(c, "MODEL_FILE", tmp_path / "m.joblib")
    monkeypatch.setattr(c, "META_FILE", tmp_path / "m.json")
    rng = np.random.default_rng(0)
    X, y, g = [], [], []
    for obj in range(6):   # 6 objects per class, 5 frames each
        for cls, length in (("car", 4.0), ("pedestrian", 0.4)):
            for _ in range(5):
                v = rng.normal(0, 0.05, NF); v[0] += length; v[6] += length   # length_m, track_max_length_m
                X.append(v.tolist()); y.append(cls); g.append(f"{cls}-{obj}")
    meta = MLClassifier().train(X, y, g)
    assert meta["cv_grouped_by"] == "tracked object" and meta["objects"] == {"car": 6, "pedestrian": 6}
    assert set(meta["per_class"]) == {"car", "pedestrian"} and meta["cv_accuracy"] > 0.9


def test_segments_feature_counts_gaps():
    from backend.processing import cluster_features
    x = np.r_[np.linspace(0, 0.1, 6), np.linspace(0.4, 0.5, 6)]   # two legs 0.3 m apart
    f = cluster_features(x, np.zeros_like(x) + 2, np.zeros_like(x), np.full_like(x, 2), np.full(12, 100))
    assert f[-2] == 2   # n_segments (last is coverage)


def test_api_smoke():
    from fastapi.testclient import TestClient
    from backend.app import app
    with TestClient(app) as cl:
        s = cl.get("/api/status").json()
        assert "lidar" in s and s["lidar"]["state"] in {"disconnected", "connecting", "error", "scanning"}
        assert cl.patch("/api/config", json={"nope": 1}).status_code == 400
        assert cl.post("/api/lidar/frequency", json={"hz": 20}).status_code == 400
        assert cl.get("/api/detections").status_code == 200
        assert cl.post("/api/ml/train", json={"data": "bogus"}).status_code == 400


def test_label_and_follow_through_engine():
    from backend.engine import Engine
    e = Engine()                         # BANTAY_DATA points at a temp dir (conftest)
    t = warm(e.bg)
    for i in range(5):                   # object appears and gets confirmed
        t += 0.1
        e._process(ring_scan(t, [(-2 + 0.2 * i, 3.0, 1.5)]))
    tid = e.frame["tracks"][0]["id"]
    e.label_track(tid, "car", follow=True)
    for i in range(5, 25):               # 2 s more: follow captures every 0.5 s
        t += 0.1
        e._process(ring_scan(t, [(-2 + 0.2 * i, 3.0, 1.5)]))
    s = e.db.samples()
    assert 4 <= len(s) <= 6 and {x["object"] for x in s} == {f"{e.started}:{tid}"}
    assert all("n_segments" in x["features"] for x in s)
    e.label_track(tid, "motorcycle")     # relabel applies to the whole object
    assert {x["label"] for x in e.db.samples()} == {"motorcycle"}
    assert e.status()["dataset"]["motorcycle"]["objects"] == 1


def test_replay_transport_controls():
    import json, time
    from backend.config import DATA
    from backend.engine import Engine
    name = "transport-test.jsonl"
    with open(DATA / "recordings" / name, "w") as f:
        for i in range(30):
            f.write(json.dumps(ring_scan(1000 + i * 0.1, [(-2 + 0.2 * i, 3.0, 1.5)]).to_json()) + "\n")
    e = Engine()
    e.cfg.update({"source": "replay", "replay_file": name, "record": True, "background_enabled": False})
    e.start()
    r = e.replay
    wait = lambda cond: next((True for _ in range(100) if cond() or time.sleep(0.05)), False)
    assert wait(lambda: r.state()["pos"] >= 3)
    r.pause(); time.sleep(0.4)
    p = r.state()["pos"]; time.sleep(0.4)
    assert r.state()["pos"] == p and r.state()["paused"]          # paused really stops
    r.step(2); assert wait(lambda: r.state()["pos"] == p + 2)       # step advances exactly
    r.seek(20); r.step(1); assert wait(lambda: r.state()["pos"] == 21)
    assert wait(lambda: e.frame and e.frame.get("replay") and e.frame["replay"]["rec_t"] == 1000 + 20 * 0.1)
    with __import__("pytest").raises(ValueError):
        r.set_speed(7)
    r.set_speed(2.0); r.play()
    assert wait(lambda: r.state()["message"] == "finished")
    assert e.db.detections()[0] == 0                                 # replays never write history
    assert not any(p.name != name for p in (DATA / "recordings").glob("*.jsonl"))   # nor re-record


def test_pointnet_grouped_training(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    import backend.pointnet as pn
    monkeypatch.setattr(pn, "MODEL_FILE", tmp_path / "p.pt")
    monkeypatch.setattr(pn, "META_FILE", tmp_path / "p.json")
    monkeypatch.setattr(pn, "EPOCHS", 60)
    rng = np.random.default_rng(1)
    pts, X, y, g = [], [], [], []
    for obj in range(10):                       # 10 objects per class (the minimum), 3 frames each
        for cls in ("car", "pedestrian"):
            for _ in range(3):
                if cls == "car":                # long straight side
                    p = np.column_stack([np.linspace(0, 3.5, 40), rng.normal(0, 0.02, 40)])
                else:                           # two small leg blobs
                    p = np.r_[rng.normal([0, 0], 0.03, (8, 2)), rng.normal([0.35, 0], 0.03, (8, 2))]
                pts.append(p.tolist()); X.append(rng.normal(0, 1, NF).tolist())   # features carry no signal here
                y.append(cls); g.append(f"{cls}-{obj}")
    with pytest.raises(ValueError):            # too few objects -> refused
        pn.PointNetClassifier().train(pts[:30], X[:30], y[:30], g[:30])
    clf = pn.PointNetClassifier()
    meta = clf.train(pts, X, y, g)
    assert meta["cv_grouped_by"] == "tracked object" and meta["objects"] == {"car": 10, "pedestrian": 10}
    assert meta["cv_accuracy"] > 0.9          # learned from the point shapes alone
    cat, conf, method = clf.predict(pts[0], X[0], 0.0)
    assert cat == "car" and 0 <= conf <= 1 and method == "ml_experimental"
    reloaded = pn.PointNetClassifier()        # checkpoint round-trips with weights_only loading
    assert reloaded.available and reloaded.predict(pts[3], X[3], 0.0)[0] == "pedestrian"


def test_background_absorbs_parked_but_not_passing():
    cfg = dict(CFG, background_absorb_s=10.0)          # window 20 s
    bg = Background()
    t = 0.0
    for i in range(30):                                 # 60 s; a car passes through the same spot every 10 s
        objs = [(0.0, 3.0, 1.5)] if i % 5 == 0 else []
        process(ring_scan(t, objs), cfg, bg); t += 2.0
    _, cl = process(ring_scan(t, [(0.0, 3.0, 1.5)]), cfg, bg)
    assert len(cl) == 1, "passing traffic must never become background"
    for _ in range(12):                                 # now it parks for 24 s (> absorb time)
        process(ring_scan(t, [(0.0, 3.0, 1.5)]), cfg, bg); t += 2.0
    _, cl = process(ring_scan(t, [(0.0, 3.0, 1.5)]), cfg, bg)
    assert cl == [], "an object parked longer than absorb_s becomes background"
    _, cl = process(ring_scan(t + 0.1, [(0.0, 3.0, 1.5), (1.0, -2.0, 0.2)]), cfg, bg)
    assert len(cl) == 1, "a new object next to the parked car is still detected"


def test_ml_features_ignore_location():
    from backend.classify import ML_FEATURES
    from backend.processing import cluster_features
    assert len(ML_FEATURES) == NF
    assert not {"range_m", "n_points", "angular_span_deg", "density_pts_per_m", "track_age"} & set(ML_FEATURES)

    def obj(dist):   # the same 1 m wide object facing the sensor, at a given distance
        a = np.arange(0, 360, 0.54)
        th = np.radians(-a)
        xs, ys = dist * np.cos(th), dist * np.sin(th)
        m = (np.abs(ys) < 0.5) & (xs > 0)
        return cluster_features(xs[m], ys[m], a[m], np.full(m.sum(), dist), np.full(m.sum(), 100))
    near, far = obj(2.0), obj(6.0)
    assert near[0] > 2.5 * far[0]                       # raw point count falls with distance...
    assert abs(near[-1] - far[-1]) < 0.1                 # ...coverage does not


def test_unplug_detection_rules():
    from backend.engine import Engine
    e = Engine()
    e.cfg.update({"source": "serial"})
    assert e.unplugged_for(now=1000.0) is None          # never connected: keep running
    e.driver.status.last_scan_t = 1000.0
    assert e.unplugged_for(now=1001.0) is None          # scanning normally
    assert e.unplugged_for(now=1010.0) == 10.0          # gone for 10 s
    e.cfg.update({"source": "replay"})
    assert e.unplugged_for(now=1010.0) is None          # replay never triggers exit


def test_empty_scan_during_spin_up():
    s = ring_scan(0)
    s.dist_mm[:] = 0                                    # T-mini Plus spin-up: every distance invalid
    frame, clusters = process(s, CFG, Background())
    assert frame["n_valid"] == 0 and clusters == []

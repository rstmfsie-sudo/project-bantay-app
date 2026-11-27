"""Range finders, alerts, fast-object tracking and the digital twin."""
import numpy as np

from backend.config import DEFAULTS

CFG = dict(DEFAULTS)




def test_fast_car_is_tracked():
    from backend.tracking import Tracker
    tr, t, out = Tracker(), 0.0, []
    for i in range(10):                                  # 14 m/s at 6 Hz: 2.33 m per scan
        x = -8 + 14 * t
        c = {"id": 0, "cx": x, "cy": 3.0, "xmin": x - 2, "xmax": x + 2, "ymin": 2.9, "ymax": 3.1,
             "features": {"length_m": 4.0}}
        out = tr.step([c], t, CFG)
        t += 1 / 6
    assert len(out) == 1 and out[0].hits == 10
    d = Tracker.describe(out[0], CFG)
    assert abs(d["speed"] - 14) < 2


def test_twin_slices():
    from backend.twin import model, slice_at
    car = slice_at(model("car"), 0.6)
    assert any(r[1] - r[0] > 4.0 for r in car)           # body: one long rectangle
    bike = slice_at(model("bicycle"), 0.2)               # below the axles: rims cut twice per wheel
    assert sum(1 for r in bike if r[1] - r[0] < 0.1) >= 4
    assert slice_at(model("pedestrian"), 1.7) == []      # above a person's head



def test_driver_threads_can_be_joined():
    # regression: storing an Event in Thread._stop broke Thread.join() (closing the app could not stop the motor)
    from backend.config import Config
    from backend.rangefinders import RangefinderLink
    from backend.ydlidar import LidarDriver
    cfg = Config()
    cfg.update({"source": "replay", "rf_enabled": False})
    for t in (LidarDriver(cfg.get, lambda s: None), RangefinderLink(cfg.get)):
        t.start()
        t.stop()
        t.join(timeout=5)
        assert not t.is_alive()


def test_simulation_source_is_isolated():
    import time
    import pytest
    from backend.engine import Engine
    e = Engine()
    e.cfg.update({"source": "simulation", "mount_height_m": 0.6, "record": True})
    e.sim.next_spawn = 0
    e.start()
    deadline = time.time() + 25
    while time.time() < deadline and not (e.frame and e.frame["source"] == "simulation" and e.frame["tracks"]):
        time.sleep(0.2)
    assert e.frame and e.frame["source"] == "simulation" and e.frame["tracks"], "simulation produced no tracked objects"
    with pytest.raises(ValueError):
        e.label_track(e.frame["tracks"][0]["id"], "car")          # never into the real dataset
    time.sleep(1.5)
    assert e.db.detections()[0] == 0                                 # never into Data History
    from backend.config import DATA
    assert not list((DATA / "recordings").glob("scan-*.jsonl"))      # never recorded
    e.cfg.update({"source": "replay", "record": False})


def test_twin_dataset_and_models(tmp_path, monkeypatch):
    import numpy as np
    import backend.classify as c
    import backend.twin as tw
    monkeypatch.setattr(tw, "DATASET_FILE", tmp_path / "twin.joblib")
    monkeypatch.setattr(c, "MODEL_FILE", tmp_path / "rf.joblib")
    monkeypatch.setattr(c, "META_FILE", tmp_path / "rf.json")
    meta = tw.build_dataset(0.6, 8)
    assert set(meta["classes"]) == {"car", "motorcycle", "bicycle", "pedestrian"} and tw.dataset_meta() == meta
    ds = tw.load_dataset()
    assert len(ds["X"]) == len(ds["y"]) == len(ds["groups"]) == len(ds["points"]) > 0
    assert all(g.startswith("twin:") for g in ds["groups"])            # never confused with real object ids
    no_int = [i for i, n in enumerate(c.ML_FEATURES) if n != "mean_intensity"]
    rf = c.MLClassifier()
    m = rf.train(ds["X"], ds["y"], ds["groups"], cols=no_int, data="twin")
    assert m["training_data"] == "twin" and "mean_intensity" not in m["features"] and m["real_cv"] is None
    from backend.processing import FEATURE_NAMES
    d = {"features": dict(zip(FEATURE_NAMES, [0] * len(FEATURE_NAMES))), "max_length": 4.4, "speed": 8.0}
    d["features"].update(length_m=4.2, width_m=1.6, coverage=0.9, n_segments=1)
    assert rf.predict(d, 0.0)[2] == "ml_synthetic"                  # twin-trained results are labelled as such
    # "both": real rows are scored on their own, held out of each fold
    X = ds["X"] + [list(ds["X"][0])] * 6
    y = ds["y"] + [ds["y"][0]] * 6
    g = ds["groups"] + [f"real:{i}" for i in range(6)]
    real = [False] * len(ds["y"]) + [True] * 6
    m2 = rf.train(X, y, g, cols=no_int, real=real, data="both")
    assert m2["real_cv"]["samples"] == 6


def test_ml_job_trains_and_activates(tmp_path, monkeypatch):
    import time
    import backend.classify as c
    import backend.pointnet as pn
    import backend.twin as tw
    from backend.engine import Engine
    monkeypatch.setattr(tw, "DATASET_FILE", tmp_path / "twin.joblib")
    monkeypatch.setattr(c, "MODEL_FILE", tmp_path / "rf.joblib"); monkeypatch.setattr(c, "META_FILE", tmp_path / "rf.json")
    monkeypatch.setattr(pn, "MODEL_FILE", tmp_path / "nn.pt"); monkeypatch.setattr(pn, "META_FILE", tmp_path / "nn.json")
    monkeypatch.setattr(pn, "EPOCHS", 15)
    e = Engine()
    e.cfg.update({"classifier": "rules"})
    e.start_ml_job("twin", ["rf", "nn"], 0.6, 12, activate=True)
    deadline = time.time() + 300
    while e.ml_job["running"] and time.time() < deadline:
        time.sleep(0.5)
    j = e.ml_job
    assert not j["running"] and j["message"].startswith("done"), j["message"]
    assert "error" not in j["results"]["rf"] and j["best"] in ("rf", "nn")
    assert e.cfg.get()["classifier"] == ("ml" if j["best"] == "rf" else "nn")
    assert e.status()["twin_dataset"]["generator"] == tw.GENERATOR

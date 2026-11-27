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


def test_twin_training_small(tmp_path, monkeypatch):
    import backend.twin as tw
    monkeypatch.setattr(tw, "MODEL_FILE", tmp_path / "t.joblib")
    monkeypatch.setattr(tw, "META_FILE", tmp_path / "t.json")
    real = [{"fv": [4.0, 0.5, 0.95, 2, 1.0, 100, 4.2, 9.0], "label": "car"}]
    m = tw.TwinModel().train(0.6, 12, real)
    assert m["cv_grouped_by"] == "simulated object" and "mean_intensity" not in m["features"]
    assert m["sim_to_real"]["samples"] == 1               # scored on the real labels too
    assert m["cv_accuracy"] > 0.7
    model = tw.TwinModel()
    cat, conf, method = model.predict(real[0]["fv"], 0.0)
    assert method == "ml_synthetic" and cat in m["classes"]


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

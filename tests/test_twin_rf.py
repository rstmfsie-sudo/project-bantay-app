"""Range finders, alerts, fast-object tracking and the digital twin."""
import numpy as np

from backend.config import DEFAULTS
from backend.rangefinders import parse_line

CFG = dict(DEFAULTS)


def test_rf_parse_line():
    f = parse_line("$RF,1234,250,-1,90,800,-1,65535*\r\n")
    assert f["ms"] == 1234 and f["L"] == (2.5, 800) and f["R"] == (None, None) and f["C"] == (0.9, 65535)
    for bad in ("$RF,1,2,3*", "RF,1,2,3,4,5,6,7*", "$RF,1,2,x,4,5,6,7*", "$RF,1,2,3,4,5,6,7"):
        assert parse_line(bad) is None


def test_alert_sides_and_beams():
    from backend.engine import Engine
    e = Engine()
    cfg = dict(CFG, angle_offset_deg=90.0)              # front points up (+y)
    track = lambda bearing, risk: {"risk": risk, "bearing": bearing, "category": "car", "id": 1, "range": 1.5}
    frame = lambda *ts: {"t_wall": 100.0, "tracks": list(ts)}
    a = e.compute_alert(cfg, frame(track(150, "WARNING")), None, 100.0)    # 60 deg left of front
    assert (a["L"], a["R"], a["S"]) == (2, 0, 2)
    a = e.compute_alert(cfg, frame(track(90, "CAUTION")), None, 100.0)     # straight ahead -> both sides
    assert (a["L"], a["R"]) == (1, 1)
    a = e.compute_alert(cfg, frame(track(150, "WARNING")), None, 105.0)    # stale frame is ignored
    assert a["S"] == 0
    beams = {"L": {"m": None}, "R": {"m": 1.2}, "C": {"m": 3.5}}           # R warning, C caution
    a = e.compute_alert(cfg, None, beams, 100.0)
    assert (a["L"], a["R"], a["S"]) == (1, 2, 2)


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

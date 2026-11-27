from backend.config import DEFAULTS
from backend.fusion import beam_hit, fuse
from backend.vehicle import sensor_geometry

CFG = {**DEFAULTS, "angle_offset_deg": 0.0, "source": "serial"}


def track(box):
    return {"box": box, "range": 2.0}


def test_beam_hit_and_miss():
    assert abs(beam_hit((0, 0), 0, (2, -0.2, 2.5, 0.2), pad=0) - 2.0) < 1e-9
    assert beam_hit((0, 0), 0, (2, 1.0, 2.5, 1.5), pad=0) is None
    assert beam_hit((0, 0), 180, (2, -0.2, 2.5, 0.2), pad=0) is None      # behind the sensor


def test_confirmed_mismatch_and_none():
    geo = sensor_geometry(CFG)                                            # left sensor looks +y from (0, 0.38)
    box = (-0.2, 2.0, 0.2, 2.5)
    f = fuse(track(box), CFG, geo, {"L": 1.52})                           # entry 2.0 - 0.38 mount, less 0.10 padding
    assert f["status"] == "confirmed" and abs(f["delta_m"]) < 0.01
    assert fuse(track(box), CFG, geo, {"L": 3.5})["status"] == "mismatch"
    assert fuse(track(box), CFG, geo, {"L": None}) is None
    assert fuse(track((5, 5, 6, 6)), CFG, geo, {"L": 1.5}) is None


def test_rotated_mounting():
    cfg = {**CFG, "angle_offset_deg": 90.0}
    geo = sensor_geometry(cfg)               # plot frame is rotated 90 deg, the left beam now points to -x
    f = fuse(track((-2.5, -0.2, -2.0, 0.2)), cfg, geo, {"L": 1.62})
    assert f and f["status"] == "confirmed"

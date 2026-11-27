"""Per-object fusion: a TF-Luna beam that lands on a tracked LiDAR object gives that object a precise range.

The LiDAR sees every object but its range is a cluster-box estimate; a TF-Luna sees only along its beam but its
distance is accurate. If a beam crosses a track's box we compare the two distances along the beam:
  agree (within fusion_tol_m)  -> status "confirmed", range_fused = LiDAR range shifted by the measured difference
  disagree                     -> status "mismatch" (TF-Luna sees something else); the LiDAR range is left alone
Mounting geometry is UNCALIBRATED by default, so agreement is only as good as the measured mounting values.
"""
from __future__ import annotations

import math

PAD_M = 0.10          # box padding: cluster boxes are tight and the TF-Luna beam has a ~2 deg field of view


def beam_hit(origin, angle_deg, box, pad=PAD_M):
    """Distance along a ray (plot frame) to where it enters box [xmin, ymin, xmax, ymax], or None if it misses."""
    a = math.radians(angle_deg)
    d = (math.cos(a), math.sin(a))
    lo = [box[0] - pad, box[1] - pad]
    hi = [box[2] + pad, box[3] + pad]
    t0, t1 = 0.0, math.inf
    for i in (0, 1):
        if abs(d[i]) < 1e-9:
            if not lo[i] <= origin[i] <= hi[i]:
                return None
            continue
        ta, tb = (lo[i] - origin[i]) / d[i], (hi[i] - origin[i]) / d[i]
        t0, t1 = max(t0, min(ta, tb)), min(t1, max(ta, tb))
        if t0 > t1:
            return None
    return t0


def fuse(d: dict, cfg: dict, geometry: dict, tfl: dict):
    """Fusion record for one described track `d` (None if no fresh TF-Luna beam crosses it).

    geometry: vehicle.sensor_geometry(cfg); tfl: {pos: metres or None} of fresh valid readings.
    Returns {sensor, tfl_m, lidar_m, delta_m, status, simulated}; d["range_fused"] is not set here.
    """
    best = None
    for pos, m in tfl.items():
        if m is None or pos not in geometry:
            continue
        g = geometry[pos]
        t = beam_hit((g["x"], g["y"]), g["angle"], d["box"])
        if t is None:
            continue
        delta = m - t
        rec = {"sensor": pos, "tfl_m": m, "lidar_m": t, "delta_m": delta,
               "status": "confirmed" if abs(delta) <= cfg["fusion_tol_m"] else "mismatch",
               "simulated": cfg["source"] == "simulation"}
        if best is None or abs(delta) < abs(best["delta_m"]):      # several beams: keep the best-agreeing one
            best = rec
    return best

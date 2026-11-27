"""Vehicle coordinate system and obstacle monitoring.

VEHICLE FRAME (V): origin at the vehicle centre, x forward, y to the LEFT (right-handed, angles CCW from forward).
PLOT FRAME (P):    the LiDAR at the origin, the frame the UI draws in; the vehicle's forward axis points along
                   `angle_offset_deg` (the same rotation that aligns the LiDAR's front with the vehicle).
    P = R(front) . (V - lidar_mount)          V = R(-front) . P + lidar_mount

Every mounting value (LiDAR and TF-Luna positions/yaws, vehicle size) is a configurable, UNCALIBRATED default
until the researcher measures it; `geometry_calibrated` stays False until they say so.
Thresholds are configurable research values, NOT certified vehicle-safety limits.
"""
from __future__ import annotations

import math

LEVELS = ["SAFE", "CAUTION", "WARNING", "DANGER"]
SIDES = ("front", "left", "right", "rear")
TFL_SIDE = {"L": "left", "R": "right", "B": "rear"}
TFL_NAMES = {"L": "TF-Luna Left", "R": "TF-Luna Right", "B": "TF-Luna Rear"}


def _rot(x, y, deg):
    a = math.radians(deg)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def to_plot(vx, vy, cfg):
    return _rot(vx - cfg["lidar_mount_x_m"], vy - cfg["lidar_mount_y_m"], cfg["angle_offset_deg"])


def to_vehicle(px, py, cfg):
    x, y = _rot(px, py, -cfg["angle_offset_deg"])
    return x + cfg["lidar_mount_x_m"], y + cfg["lidar_mount_y_m"]


def sensor_geometry(cfg) -> dict:
    """Each TF-Luna's mount point and beam direction in the PLOT frame (what the UI draws)."""
    out = {}
    for pos in ("L", "R", "B"):
        x, y = to_plot(cfg[f"tfl_{pos}_x_m"], cfg[f"tfl_{pos}_y_m"], cfg)
        out[pos] = {"x": x, "y": y, "angle": cfg["angle_offset_deg"] + cfg[f"tfl_{pos}_yaw_deg"],
                    "yaw_vehicle": cfg[f"tfl_{pos}_yaw_deg"], "mount_vehicle": [cfg[f"tfl_{pos}_x_m"], cfg[f"tfl_{pos}_y_m"]]}
    return out


def level_for(dist_m, cfg) -> int:
    if dist_m is None:
        return 0
    if dist_m <= cfg["prox_danger_m"]:
        return 3
    if dist_m <= cfg["prox_warning_m"]:
        return 2
    if dist_m <= cfg["prox_caution_m"]:
        return 1
    return 0


def clearance(vx, vy, cfg) -> float:
    """Distance from a point (vehicle frame) to the vehicle's body rectangle (0 inside it)."""
    dx = max(abs(vx) - cfg["vehicle_length_m"] / 2, 0.0)
    dy = max(abs(vy) - cfg["vehicle_width_m"] / 2, 0.0)
    return math.hypot(dx, dy)


def sector(vx, vy) -> str:
    a = math.degrees(math.atan2(vy, vx))
    if -45 <= a <= 45:
        return "front"
    if 45 < a < 135:
        return "left"
    if -135 < a < -45:
        return "right"
    return "rear"


def monitor(cfg, frame_points, tfl: dict) -> dict:
    """Nearest obstacle per side from BOTH sources, as clearance from the vehicle body.

    frame_points: iterable of (x, y, kind) in the plot frame from a FRESH LiDAR frame (kind >= 1 = foreground,
                  i.e. new/moving objects, so the vehicle's own static surroundings do not trigger warnings),
                  or None if the LiDAR data is not fresh.
    tfl:          {pos: metres or None}, fresh valid readings only. A TF-Luna gives a distance along its beam
                  from its face; it says nothing about what the object is.
    Each side reports one nearest distance, so an obstacle seen by both a TF-Luna and the LiDAR is never
    counted twice.
    """
    lidar = {s: None for s in SIDES}
    if frame_points is not None:
        for x, y, k in frame_points:
            if k < 1:
                continue
            vx, vy = to_vehicle(x, y, cfg)
            c = clearance(vx, vy, cfg)
            if c <= 0.05:                            # inside / on the body: the vehicle itself or noise
                continue
            s = sector(vx, vy)
            if lidar[s] is None or c < lidar[s]:
                lidar[s] = c
    pos_for_side = {side: pos for pos, side in TFL_SIDE.items()}      # front has no TF-Luna
    sides = {}
    for s in SIDES:
        p = pos_for_side.get(s)
        t = tfl.get(p) if p else None
        cand = []
        if t is not None:
            cand.append((t, TFL_NAMES[p]))
        if lidar[s] is not None:
            cand.append((lidar[s], "YDLIDAR"))
        nearest, src = min(cand) if cand else (None, None)
        lvl = level_for(nearest, cfg)
        sides[s] = {"level": lvl, "label": LEVELS[lvl], "nearest_m": nearest, "source": src,
                    "lidar_m": lidar[s], "tfl_m": t}
    overall = max(v["level"] for v in sides.values())
    return {"sides": sides, "overall": overall, "overall_label": LEVELS[overall]}

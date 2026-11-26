"""Multi-object tracking: constant-velocity Kalman filter per track, gated Hungarian assignment.

Speed is only reported after cfg["speed_min_frames"] updates, and the UI marks it as an estimate:
a cluster centroid moves as the visible side of an object changes, which biases speed.
Risk level reuses the BANTAY proposal logic (zone ranges + time-to-collision, Hayward 1972),
measured here relative to the sensor.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import linear_sum_assignment

F_Q = 1.0      # process noise (m/s^2)^2
R_MEAS = 0.10  # centroid measurement noise std (m)


class Track:
    _next = 1

    def __init__(self, c, t):
        self.id = Track._next
        Track._next += 1
        self.x = np.array([c["cx"], c["cy"], 0.0, 0.0])
        self.P = np.diag([R_MEAS**2, R_MEAS**2, 4.0, 4.0])
        self.first_t = self.last_t = t
        self.hits, self.missed = 1, 0
        self.max_length = c["features"]["length_m"]
        self.cluster = c
        self.trail = [(c["cx"], c["cy"])]
        self.category, self.method, self.confidence, self.note = "unknown", "detection", None, ""
        self.last_logged = 0.0

    def predict(self, dt):
        F = np.eye(4); F[0, 2] = F[1, 3] = dt
        G = np.array([[dt**2 / 2, 0], [0, dt**2 / 2], [dt, 0], [0, dt]])
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + G @ G.T * F_Q

    def update(self, c, t):
        H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], float)
        S = H @ self.P @ H.T + np.eye(2) * R_MEAS**2
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ (np.array([c["cx"], c["cy"]]) - H @ self.x)
        self.P = (np.eye(4) - K @ H) @ self.P
        self.hits += 1
        self.missed = 0
        self.last_t = t
        self.cluster = c
        self.max_length = max(self.max_length, c["features"]["length_m"])
        self.trail = (self.trail + [(c["cx"], c["cy"])])[-40:]


def risk_level(r, ttc, cfg):
    """Proposal classification: WARNING inside Z1 or TTC < 1.5 s, CAUTION inside Z2 or TTC < 3 s."""
    if r <= cfg["zone_warning_m"] or (ttc is not None and ttc <= cfg["ttc_warning_s"]):
        return "WARNING"
    if r <= cfg["zone_caution_m"] or (ttc is not None and ttc <= cfg["ttc_caution_s"]):
        return "CAUTION"
    return "CLEAR"


class Tracker:
    def __init__(self):
        self.tracks: list[Track] = []
        self.last_t = None

    def reset(self):
        self.tracks, self.last_t = [], None

    def step(self, clusters, t, cfg) -> list[Track]:
        dt = 0.0 if self.last_t is None else min(t - self.last_t, 1.0)
        self.last_t = t
        for tr in self.tracks:
            tr.predict(dt)
        matched_c = set()
        if self.tracks and clusters:
            cost = np.array([[np.hypot(tr.x[0] - c["cx"], tr.x[1] - c["cy"]) for c in clusters]
                             for tr in self.tracks])
            # a young track has no velocity estimate yet: allow the distance a road vehicle can cover in one
            # scan (at 6 Hz a 14 m/s car moves ~2.3 m), otherwise fast traffic is never tracked
            young = max(cfg["track_gate_m"], cfg["track_max_speed_mps"] * dt)
            for i, j in zip(*linear_sum_assignment(cost)):
                if cost[i, j] <= (cfg["track_gate_m"] if self.tracks[i].hits >= 3 else young):
                    self.tracks[i].update(clusters[j], t)
                    matched_c.add(j)
        for tr in self.tracks:
            if tr.last_t != t:
                tr.missed += 1
        self.tracks = [tr for tr in self.tracks if tr.missed <= cfg["track_max_missed"]]
        self.tracks += [Track(c, t) for j, c in enumerate(clusters) if j not in matched_c]
        return [tr for tr in self.tracks if tr.hits >= cfg["track_confirm_hits"]]

    @staticmethod
    def describe(tr: Track, cfg) -> dict:
        x, y, vx, vy = tr.x
        r = float(np.hypot(x, y))
        speed = heading = ttc = None
        if tr.hits >= cfg["speed_min_frames"]:
            speed = float(np.hypot(vx, vy))
            if speed > 0.3:
                heading = float(np.degrees(np.arctan2(vy, vx)))
            closing = -(x * vx + y * vy) / r if r > 0 else 0.0
            if closing > 0.1:
                ttc = r / closing
        return {
            "id": tr.id, "x": float(x), "y": float(y), "range": r,
            "bearing": float(np.degrees(np.arctan2(y, x))),
            "speed": speed, "heading": heading, "ttc": ttc,
            "risk": risk_level(r, ttc, cfg),
            "duration": tr.last_t - tr.first_t, "age": tr.hits, "missed": tr.missed,
            "max_length": tr.max_length, "features": tr.cluster["features"],
            "category": tr.category, "method": tr.method, "confidence": tr.confidence, "note": tr.note,
            "trail": [[round(a, 3), round(b, 3)] for a, b in tr.trail],
            "box": [tr.cluster[k] for k in ("xmin", "ymin", "xmax", "ymax")],
            "cluster_id": tr.cluster["id"],
        }

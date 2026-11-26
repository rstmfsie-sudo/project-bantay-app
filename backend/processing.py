"""Scan processing: validation -> filtering -> polar/Cartesian -> background removal -> DBSCAN -> features.

Angle convention: the sensor reports angles clockwise in degrees (manual 3.1.5). With cfg["mirror"]
the angle is negated so x = r*cos(theta), y = r*sin(theta) gives the usual counter-clockwise plot
(the sensor's 0 deg on +x). angle_offset_deg rotates the whole scan to match how it is mounted.
"""
from __future__ import annotations


import numpy as np
from sklearn.cluster import DBSCAN

BINS = 720   # 0.5 deg background bins (T-mini Plus resolution is ~0.54 deg at 6 Hz)

FEATURE_NAMES = ["n_points", "length_m", "width_m", "linearity", "range_m",
                 "angular_span_deg", "density_pts_per_m", "mean_intensity", "n_segments", "coverage"]
SEGMENT_GAP_M = 0.12   # a gap this wide along the object splits it (legs, bicycle wheels)


def to_xy(angles_deg, r_m, cfg):
    th = np.radians((-angles_deg if cfg["mirror"] else angles_deg) + cfg["angle_offset_deg"])
    return r_m * np.cos(th), r_m * np.sin(th)


def valid_mask(scan, cfg):
    r = scan.dist_mm / 1000.0
    m = (scan.dist_mm > 0) & (r >= cfg["range_min_m"]) & (r <= cfg["range_max_m"])
    if cfg["drop_flagged"]:
        m &= scan.flag < 2
    if cfg["min_intensity"]:
        m &= scan.intensity >= cfg["min_intensity"]
    return m


class Background:
    """Self-updating model of the static scene, for a road that keeps changing.

    Every SNAPSHOT_S seconds the nearest return per 0.5-degree bin is stored; the background is the
    per-bin median over the last `absorb_s * 2` seconds. Consequences:
      * anything that moves (traffic, people) never becomes background,
      * anything that stays still for about `absorb_s` (a parked car, a new sign) is absorbed automatically,
      * a bin with no return is +inf ("open"), so occasional passing objects cannot become background.
    No manual learning step is needed; it is ready a few seconds after start.
    """

    SNAPSHOT_S = 2.0
    MIN_SNAPSHOTS = 5

    def __init__(self):
        self.snaps: list[np.ndarray] = []
        self.ranges: np.ndarray | None = None
        self.last_t: float | None = None

    @property
    def ready(self):
        return self.ranges is not None

    @property
    def progress(self):
        return min(1.0, len(self.snaps) / self.MIN_SNAPSHOTS)

    def reset(self):
        self.snaps, self.ranges, self.last_t = [], None, None

    @staticmethod
    def bins(angles):
        return (np.asarray(angles) * BINS / 360.0).astype(int) % BINS

    def observe(self, angles, r, t, absorb_s):
        if self.last_t is not None and t < self.last_t:   # time went backwards (replay seek)
            self.reset()
        if self.last_t is not None and t - self.last_t < self.SNAPSHOT_S:
            return
        self.last_t = t
        per = np.full(BINS, np.inf)
        b = self.bins(angles)
        np.minimum.at(per, b, r)                          # nearest return per bin
        keep = max(self.MIN_SNAPSHOTS, int(2 * absorb_s / self.SNAPSHOT_S))
        self.snaps = (self.snaps + [per])[-keep:]
        if len(self.snaps) >= self.MIN_SNAPSHOTS:
            self.ranges = np.median(np.vstack(self.snaps), axis=0)

    def foreground(self, angles, r, margin):
        if self.ranges is None:                           # still warming up: report nothing rather than everything
            return np.zeros(len(r), bool)
        return r < self.ranges[self.bins(angles)] - margin


def cluster_features(x, y, angles, r, intensity, step_deg=0.54) -> list[float]:
    """Geometry of one object. `coverage` = share of the laser beams crossing the object that returned from it:
    unlike point count or density it does not change with distance, so it describes the object, not the site."""
    pts = np.column_stack([x, y])
    c = pts.mean(0)
    segments = 1
    if len(pts) >= 3:
        ev, evec = np.linalg.eigh(np.cov((pts - c).T))
        proj = (pts - c) @ evec
        length, width = np.ptp(proj[:, 1]), np.ptp(proj[:, 0])   # eigh: ascending eigenvalues
        linearity = float(1 - ev[0] / ev[1]) if ev[1] > 1e-9 else 0.0
        segments = int(1 + np.sum(np.diff(np.sort(proj[:, 1])) > SEGMENT_GAP_M))
    else:
        length = float(np.hypot(*np.ptp(pts, 0))); width, linearity = 0.0, 0.0
    a = np.radians(angles)
    span = np.degrees(np.ptp(np.unwrap(a))) if len(a) > 1 else 0.0
    coverage = min(1.0, len(pts) / (span / step_deg + 1))
    return [float(len(pts)), float(length), float(width), linearity, float(np.hypot(*c)),
            float(span), float(len(pts) / max(length, 0.05)), float(np.mean(intensity)), float(segments), float(coverage)]


def process(scan, cfg, background: Background):
    """Returns (frame dict for the UI, list of cluster dicts). Original scan is never modified."""
    valid = valid_mask(scan, cfg)
    a, r = scan.angles[valid], scan.dist_mm[valid] / 1000.0
    inten = scan.intensity[valid]
    if cfg["background_enabled"]:
        background.observe(a, r, scan.t, cfg["background_absorb_s"])
    x, y = to_xy(a, r, cfg)
    fg = background.foreground(a, r, cfg["background_margin_m"]) if cfg["background_enabled"] \
        else np.ones(len(r), bool)

    labels = np.full(len(r), -1)
    idx = np.flatnonzero(fg)
    if len(idx) >= cfg["dbscan_min_samples"]:
        labels[idx] = DBSCAN(eps=cfg["dbscan_eps_m"], min_samples=cfg["dbscan_min_samples"]) \
            .fit_predict(np.column_stack([x[idx], y[idx]]))

    clusters = []
    for k in range(labels.max(initial=-1) + 1):   # initial: a scan can have no valid points (spin-up)
        m = labels == k
        if m.sum() < cfg["min_cluster_points"]:
            labels[m] = -1
            continue
        f = cluster_features(x[m], y[m], a[m], r[m], inten[m], 360.0 / max(len(scan.angles), 1))
        cx, cy = float(x[m].mean()), float(y[m].mean())
        clusters.append({
            "id": len(clusters), "cx": cx, "cy": cy, "range": float(np.hypot(cx, cy)),
            "bearing": float(np.degrees(np.arctan2(cy, cx))),
            "xmin": float(x[m].min()), "xmax": float(x[m].max()),
            "ymin": float(y[m].min()), "ymax": float(y[m].max()),
            "features": dict(zip(FEATURE_NAMES, f)),
            "points": np.round(np.column_stack([x[m], y[m]]), 3).tolist(),
        })
        labels[m] = len(clusters) - 1

    rej = ~valid & (scan.dist_mm > 0)
    rx, ry = to_xy(scan.angles[rej], scan.dist_mm[rej] / 1000.0, cfg)
    frame = {
        "n_raw": int(len(scan.angles)), "n_valid": int(valid.sum()), "n_foreground": int(fg.sum()),
        "rejected": np.round(np.column_stack([rx, ry]), 3).tolist(),   # raw returns removed by filters
        # [x, y, kind] kind: 0 = filtered (valid) point, 1 = foreground, 2+ = cluster index + 2
        "points": np.column_stack([np.round(x, 3), np.round(y, 3),
                                   np.where(labels >= 0, labels + 2, fg.astype(int))]).tolist(),
    }
    return frame, clusters

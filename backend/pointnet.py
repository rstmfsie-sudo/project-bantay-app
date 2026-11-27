"""Experimental neural classifier: a small PointNet-style network (Qi et al., 2017) over the 2D points of
one object, combined with the same object-only features the Random Forest uses. Points are centered and
rotated onto the object's own axis, so neither its position nor its travel direction is visible to it.

Honesty rules are identical to the Random Forest:
  * evaluated with cross-validation grouped by tracked object (no frames of a test object in training),
  * only measured numbers are reported, output probability is uncalibrated,
  * "validated" is set by the researcher only.
Neural networks need much more data than a forest, so training is refused below MIN_OBJECTS objects
per category. PyTorch is optional: without it this module reports itself unavailable.
"""
from __future__ import annotations

import importlib.util
import json
import time

import numpy as np

from .classify import ML_FEATURES, NOTES, method_for, real_only_score
from .config import DATA

N_POINTS = 64
MIN_OBJECTS = 10       # per category; a network trained on fewer objects mostly memorizes
EPOCHS = 150
MODEL_FILE = DATA / "models" / "pointnet.pt"
META_FILE = DATA / "models" / "pointnet.json"


def installed() -> bool:
    return importlib.util.find_spec("torch") is not None


def prepare_points(points, rng=None) -> np.ndarray:
    """Center, rotate onto the principal axis (so travel direction does not matter), resample to N_POINTS."""
    P = np.asarray(points, np.float32)
    P = P - P.mean(0)
    if len(P) >= 3:
        _, vec = np.linalg.eigh(np.cov(P.T))
        P = P @ vec[:, ::-1]                 # major axis first
    if rng is None:                          # deterministic for inference
        idx = np.linspace(0, len(P) - 1, N_POINTS).round().astype(int)
    else:
        idx = rng.choice(len(P), N_POINTS, replace=len(P) < N_POINTS)
    return P[idx].T.copy()                   # (2, N_POINTS)


def _net(n_feat, n_cls):
    import torch.nn as nn

    class PointNetLite(nn.Module):
        def __init__(self):
            super().__init__()
            self.point_mlp = nn.Sequential(nn.Conv1d(2, 32, 1), nn.ReLU(), nn.Conv1d(32, 64, 1), nn.ReLU(),
                                           nn.Conv1d(64, 128, 1), nn.ReLU())
            self.head = nn.Sequential(nn.Linear(128 + n_feat, 64), nn.ReLU(), nn.Dropout(0.2), nn.Linear(64, n_cls))

        def forward(self, pts, feats):
            import torch
            return self.head(torch.cat([self.point_mlp(pts).max(dim=2).values, feats], dim=1))

    return PointNetLite()


def _fit(points, F, y_idx, n_cls, seed=0):
    """Train one network. points: list of (n,2) arrays; F: standardized features; y_idx: class indices."""
    import torch
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    net = _net(F.shape[1], n_cls)
    opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-4)
    counts = np.bincount(y_idx, minlength=n_cls)
    loss_fn = torch.nn.CrossEntropyLoss(weight=torch.tensor(len(y_idx) / (n_cls * np.maximum(counts, 1)), dtype=torch.float32))
    Ft, yt = torch.tensor(F, dtype=torch.float32), torch.tensor(y_idx)
    net.train()
    for _ in range(EPOCHS):
        # augmentation: random resampling, mirror flips, small jitter (sensor noise is ~2 cm)
        P = np.stack([prepare_points(p, rng) for p in points])
        P *= rng.choice([-1.0, 1.0], size=(len(P), 2, 1))
        P += rng.normal(0, 0.02, P.shape)
        opt.zero_grad()
        loss = loss_fn(net(torch.tensor(P, dtype=torch.float32), Ft), yt)
        loss.backward()
        opt.step()
    net.eval()
    return net


def _predict_proba(net, points, F):
    import torch
    with torch.no_grad():
        P = torch.tensor(np.stack([prepare_points(p) for p in points]), dtype=torch.float32)
        return torch.softmax(net(P, torch.tensor(F, dtype=torch.float32)), dim=1).numpy()


class PointNetClassifier:
    def __init__(self):
        self.net, self.meta, self.ckpt = None, None, None
        self.load()

    @property
    def installed(self):
        return installed()

    @property
    def available(self):
        return self.net is not None

    def load(self):
        if not (installed() and MODEL_FILE.exists() and META_FILE.exists()):
            return
        try:
            import torch
            ck = torch.load(MODEL_FILE, weights_only=True)
            net = _net(len(ck["mu"]), len(ck["classes"]))
            net.load_state_dict(ck["state"])
            net.eval()
            self.net, self.ckpt, self.meta = net, ck, json.loads(META_FILE.read_text())
        except Exception:
            self.net = self.meta = self.ckpt = None

    def predict(self, points, feats, min_conf):
        cols = self.meta.get("feature_index") or range(len(feats))
        feats = [feats[i] for i in cols]
        F = (np.asarray([feats], np.float32) - np.asarray(self.ckpt["mu"], np.float32)) / np.asarray(self.ckpt["sd"], np.float32)
        p = _predict_proba(self.net, [points], F)[0]
        k = int(np.argmax(p))
        return (self.ckpt["classes"][k] if p[k] >= min_conf else "unknown"), float(p[k]), method_for(self.meta)

    def set_validated(self, value: bool):
        if not self.meta:
            raise ValueError("No trained network")
        self.meta["validated"] = bool(value)
        META_FILE.write_text(json.dumps(self.meta, indent=2))

    def train(self, points, X, y, groups, cols=None, real=None, data: str = "real", progress=None) -> dict:
        if not installed():
            raise ValueError("PyTorch is not installed (pip install torch --index-url https://download.pytorch.org/whl/cpu)")
        import torch
        from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
        from sklearn.model_selection import StratifiedGroupKFold

        cols = list(range(len(ML_FEATURES))) if cols is None else list(cols)
        y, groups, X = np.asarray(y), np.asarray(groups), np.asarray(X, np.float32)[:, cols]
        real = np.full(len(y), data == "real") if real is None else np.asarray(real, bool)
        objects = {str(c): len(set(groups[y == c])) for c in np.unique(y)}
        usable = [c for c in objects if objects[c] >= MIN_OBJECTS]
        if len(usable) < 2:
            raise ValueError(f"The neural network needs at least 2 categories with {MIN_OBJECTS}+ different objects each "
                             f"(objects per category now: {objects}). Use the Random Forest until then.")
        keep = np.isin(y, usable)
        X, y, groups, real = X[keep], y[keep], groups[keep], real[keep]
        points = [p for p, k in zip(points, keep) if k]
        classes = sorted(usable)
        yi = np.array([classes.index(c) for c in y])

        k = int(min(5, min(objects[c] for c in usable)))
        pred = np.empty(len(y), dtype=int)
        for fold, (tr, te) in enumerate(StratifiedGroupKFold(k, shuffle=True, random_state=0).split(X, yi, groups)):
            if progress:
                progress(fold / (k + 1))
            mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-6      # standardize on training folds only
            net = _fit([points[i] for i in tr], (X[tr] - mu) / sd, yi[tr], len(classes))
            pred[te] = _predict_proba(net, [points[i] for i in te], (X[te] - mu) / sd).argmax(1)

        mu, sd = X.mean(0), X.std(0) + 1e-6
        net = _fit(points, (X - mu) / sd, yi, len(classes))
        pr, rc, f1, sup = precision_recall_fscore_support(yi, pred, labels=range(len(classes)), zero_division=0)
        meta = {
            "version": time.strftime("pointnet-%Y%m%d-%H%M%S"),
            "algorithm": f"PointNet-lite (2D points x{N_POINTS} + {len(X[0])} object features), {EPOCHS} epochs, Adam",
            "classes": classes, "samples": {c: int((y == c).sum()) for c in classes},
            "features": [ML_FEATURES[i] for i in cols], "feature_index": cols, "training_data": data,
            "real_cv": real_only_score(yi, pred, real if data != "real" else None, list(range(len(classes)))),
            "objects": {c: int(objects[c]) for c in classes},
            "cv_folds": k, "cv_grouped_by": "tracked object", "cv_accuracy": float(accuracy_score(yi, pred)),
            "confusion_matrix": confusion_matrix(yi, pred, labels=range(len(classes))).tolist(),
            "per_class": {c: {"precision": float(pr[i]), "recall": float(rc[i]), "f1": float(f1[i]),
                              "samples": int(sup[i])} for i, c in enumerate(classes)},
            "validated": False, "trained_at": time.time(),
            "note": NOTES[data],
        }
        # plain lists: torch.load(weights_only=True) refuses numpy arrays
        ck = {"state": net.state_dict(), "classes": classes, "mu": mu.tolist(), "sd": sd.tolist()}
        torch.save(ck, MODEL_FILE)
        META_FILE.write_text(json.dumps(meta, indent=2))
        self.net, self.ckpt, self.meta = net, ck, meta
        return meta

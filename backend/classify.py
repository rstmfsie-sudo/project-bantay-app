"""Object classification, kept strictly separate by method:

  detection     - a cluster exists; no category claimed (category "unknown")
  rule_based    - geometric heuristics below; NO confidence score is produced
  ml_experimental / ml_validated - a scikit-learn model trained on the researcher's own labeled
                  samples. Confidence = the model's class probability (uncalibrated). "validated" is
                  only set when the researcher marks the model as field-validated.

Physical limit: a single-plane 2D lidar sees one horizontal cross-section. A bicycle and a motorcycle
can look identical at that height, and any object seen end-on looks small, so the rules refuse to
separate two-wheelers and lean on track-level (temporal) evidence.
"""
from __future__ import annotations

import json
import time

import joblib
import numpy as np

from .config import DATA

CATEGORIES = ["car", "motorcycle", "bicycle", "pedestrian", "unknown"]
# Only properties of the OBJECT itself. Distance from the sensor, point count, angular span and time in view
# depend on where the sensor stands and where the lanes are; a model given those learns the site
# ("things at 3 m are cars") instead of the object, and fails at another road.
OBJECT_FEATURES = ["length_m", "width_m", "linearity", "n_segments", "coverage", "mean_intensity"]
TRACK_FEATURES = ["track_max_length_m", "track_speed_mps"]
ML_FEATURES = OBJECT_FEATURES + TRACK_FEATURES
MIN_OBJECTS = 3   # distinct tracked objects per category needed for grouped cross-validation
NOTES = {
    "real": "Grouped cross-validation on the researcher's own samples. Not field-validated until marked so.",
    "twin": "Trained and cross-validated on SIMULATED digital-twin objects only. Real-world accuracy is unknown "
            "until it is checked against real labeled objects.",
    "both": "Trained on simulated twin objects plus the researcher's real samples. 'Real objects' accuracy is "
            "measured on real objects held out of each fold; it is the number to trust.",
}
MODEL_FILE = DATA / "models" / "classifier.joblib"
META_FILE = DATA / "models" / "classifier.json"

# Rule thresholds (metres / m/s). Heuristics for the research prototype, to be calibrated on site.
CAR_MIN_LENGTH = 2.5         # no other target category gives a >= 2.5 m contiguous cross-section
TWO_WHEELER_LENGTH = (1.2, 2.5)
TWO_WHEELER_MAX_WIDTH = 0.6
PEDESTRIAN_MAX_LENGTH = 0.8
PEDESTRIAN_MAX_SPEED = 2.5   # walking/jogging; needs a measured speed, otherwise "unknown"


def rule_classify(track: dict) -> tuple[str, str]:
    """Returns (category, note). Uses the largest extent seen over the track's lifetime."""
    L, w, v = track["max_length"], track["features"]["width_m"], track.get("speed")
    if L >= CAR_MIN_LENGTH:
        return "car", "cross-section >= 2.5 m"
    if TWO_WHEELER_LENGTH[0] <= L < TWO_WHEELER_LENGTH[1] and w < TWO_WHEELER_MAX_WIDTH:
        return "unknown", "two-wheeler candidate (motorcycle/bicycle not separable by geometry)"
    if L < PEDESTRIAN_MAX_LENGTH and v is not None and v < PEDESTRIAN_MAX_SPEED:
        return "pedestrian", "small cross-section, walking speed"
    return "unknown", "insufficient evidence"


def method_for(meta: dict) -> str:
    """Live label: any simulated training data -> "ml_synthetic"; real only -> experimental/validated."""
    if meta.get("training_data", "real") != "real":
        return "ml_synthetic"
    return "ml_validated" if meta.get("validated") else "ml_experimental"


def real_only_score(y, pred, real, labels) -> dict | None:
    """Accuracy on the REAL objects alone, from grouped-CV predictions (each was held out of its fold)."""
    if real is None or not np.any(real):
        return None
    from sklearn.metrics import accuracy_score, confusion_matrix
    yr, pr = np.asarray(y)[real], np.asarray(pred)[real]
    return {"accuracy": float(accuracy_score(yr, pr)), "samples": int(real.sum()),
            "confusion_matrix": confusion_matrix(yr, pr, labels=labels).tolist()}


def feature_vector(track: dict) -> list[float]:
    f = track["features"]
    v = track.get("speed")
    return [f[k] for k in OBJECT_FEATURES] + [track["max_length"], -1.0 if v is None else v]


class MLClassifier:
    def __init__(self):
        self.model = None
        self.meta: dict | None = None
        self.load()

    def load(self):
        if MODEL_FILE.exists() and META_FILE.exists():
            try:
                self.model = joblib.load(MODEL_FILE)
                self.meta = json.loads(META_FILE.read_text())
            except Exception:
                self.model, self.meta = None, None

    @property
    def available(self):
        return self.model is not None

    def predict(self, track: dict, min_conf: float) -> tuple[str, float, str]:
        fv = feature_vector(track)
        cols = self.meta.get("feature_index") or range(len(fv))
        p = self.model.predict_proba([[fv[i] for i in cols]])[0]
        k = int(np.argmax(p))
        cat, conf = self.model.classes_[k], float(p[k])
        return (cat if conf >= min_conf else "unknown"), conf, method_for(self.meta)

    def set_validated(self, value: bool):
        if not self.meta:
            raise ValueError("No trained model")
        self.meta["validated"] = bool(value)
        META_FILE.write_text(json.dumps(self.meta, indent=2))

    def train(self, X: list[list[float]], y: list[str], groups: list[str], cols=None, real=None,
              data: str = "real") -> dict:
        """Random Forest, evaluated with cross-validation GROUPED BY OBJECT: all samples of one tracked
        object stay in the same fold, so the score is never inflated by testing on frames of an object
        the model was trained on. Reports only measured numbers.
        cols: feature columns to use; real: bool mask of real rows (for the real-only score);
        data: "real" | "twin" | "both" (what it learned from, shown in the app)."""
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
        from sklearn.model_selection import StratifiedGroupKFold, cross_val_predict

        cols = list(range(len(ML_FEATURES))) if cols is None else list(cols)
        y, groups, X = np.asarray(y), np.asarray(groups), np.asarray(X, float)[:, cols]
        real = np.full(len(y), data == "real") if real is None else np.asarray(real, bool)
        objects = {str(c): len(set(groups[y == c])) for c in np.unique(y)}
        usable = [c for c in objects if objects[c] >= MIN_OBJECTS and (y == c).sum() >= 5]
        if len(usable) < 2:
            raise ValueError(f"Need at least 2 categories, each with 5+ samples from {MIN_OBJECTS}+ different "
                             f"objects (objects per category now: {objects})")
        keep = np.isin(y, usable)
        X, y, groups, real = X[keep], y[keep], groups[keep], real[keep]
        k = int(min(5, min(objects[c] for c in usable)))
        clf = RandomForestClassifier(n_estimators=200, class_weight="balanced", random_state=0)
        pred = cross_val_predict(clf, X, y, groups=groups,
                                 cv=StratifiedGroupKFold(k, shuffle=True, random_state=0))
        clf.fit(X, y)
        cls = clf.classes_.tolist()
        pr, rc, f1, sup = precision_recall_fscore_support(y, pred, labels=cls, zero_division=0)
        meta = {
            "version": time.strftime("rf-%Y%m%d-%H%M%S"),
            "algorithm": "RandomForestClassifier(n_estimators=200, class_weight=balanced)",
            "features": [ML_FEATURES[i] for i in cols], "feature_index": cols, "classes": cls,
            "training_data": data, "real_cv": real_only_score(y, pred, real if data != "real" else None, cls),
            "samples": dict(zip(*[a.tolist() for a in np.unique(y, return_counts=True)])),
            "objects": {c: int(objects[c]) for c in cls},
            "cv_folds": k, "cv_grouped_by": "tracked object", "cv_accuracy": float(accuracy_score(y, pred)),
            "per_class": {c: {"precision": float(pr[i]), "recall": float(rc[i]), "f1": float(f1[i]),
                              "samples": int(sup[i])} for i, c in enumerate(cls)},
            "confusion_matrix": confusion_matrix(y, pred, labels=cls).tolist(),
            "feature_importance": dict(zip([ML_FEATURES[i] for i in cols], np.round(clf.feature_importances_, 4).tolist())),
            "validated": False, "trained_at": time.time(),
            "note": NOTES[data],
        }
        joblib.dump(clf, MODEL_FILE)
        META_FILE.write_text(json.dumps(meta, indent=2))
        self.model, self.meta = clf, meta
        return meta

"""All tunable settings in one place, persisted to data/config.json (same idea as the proposal's
configuration block: calibration never requires code changes)."""
import json
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The packaged app (PyInstaller) keeps data in %LOCALAPPDATA% so rebuilding the exe never touches it.
_default_data = Path(os.environ["LOCALAPPDATA"]) / "BANTAY LiDAR" if getattr(sys, "frozen", False) else ROOT / "data"
DATA = Path(os.environ.get("BANTAY_DATA", _default_data))   # override keeps test runs out of real data
DATA.mkdir(parents=True, exist_ok=True)
DATA.mkdir(exist_ok=True)
(DATA / "recordings").mkdir(exist_ok=True)
(DATA / "models").mkdir(exist_ok=True)
CONFIG_FILE = DATA / "config.json"

DEFAULTS = {
    # acquisition
    "source": "serial",           # serial | replay | simulation (digital twin, no LiDAR needed)
    "port": "auto",
    "baud": 230400,                # T-mini Plus manual / SDK Dataset.md
    "replay_file": "",
    "record": False,               # save every raw revolution to data/recordings/*.jsonl
    # geometry: the sensor reports clockwise angles; set mounting yaw so 0 deg points where you want
    "angle_offset_deg": 0.0,
    "mirror": True,                # True: convert clockwise sensor angles to the math (counter-clockwise) convention
    # filtering
    "range_min_m": 0.05,
    "range_max_m": 12.0,
    "drop_flagged": True,          # drop flag 2 (specular) and 3 (ambient light) points, manual 3.1.3
    "min_intensity": 0,
    "background_enabled": True,
    "background_margin_m": 0.25,
    "background_absorb_s": 30.0,   # something still for this long becomes background (parked car)   # a point must be this much nearer than the background to be foreground
    # clustering (DBSCAN)
    "dbscan_eps_m": 0.30,          # must exceed leg / wheel gaps so one person or bike stays one object
    "dbscan_min_samples": 4,
    "min_cluster_points": 4,
    # classification
    "classifier": "rules",         # rules | ml
    "ml_min_confidence": 0.6,      # below this the ML result becomes Unknown
    # tracking
    "track_gate_m": 1.0,
    "track_max_speed_mps": 20.0,   # fastest object to expect (sets the search radius for new tracks)
    "track_max_missed": 5,
    "track_confirm_hits": 3,
    "speed_min_frames": 6,         # do not show speed before this many updates
    # alert zones, carried over from the BANTAY proposal (Z1/Z2 and TTC thresholds)
    "zone_warning_m": 2.0,
    "zone_caution_m": 4.0,
    "ttc_warning_s": 1.5,
    "ttc_caution_s": 3.0,
    # three TF-Luna sensors on the Pico W (separate USB serial device from the LiDAR)
    "rf_enabled": True,
    "rf_port": "auto",
    # VEHICLE GEOMETRY - vehicle frame: origin at the vehicle centre, x forward, y LEFT, yaw CCW from forward.
    # All values below are UNCALIBRATED defaults for a typical motorcycle until measured on the real vehicle.
    "geometry_calibrated": False,
    "vehicle_length_m": 2.0,
    "vehicle_width_m": 0.75,
    "lidar_mount_x_m": 0.0,        # where the YDLIDAR sits on the vehicle
    "lidar_mount_y_m": 0.0,
    "tfl_L_x_m": 0.0,              # SENSOR 1 TF-Luna Left: on the left side, looking left
    "tfl_L_y_m": 0.38,
    "tfl_L_yaw_deg": 90.0,
    "tfl_R_x_m": 0.0,              # SENSOR 2 TF-Luna Right: on the right side, looking right
    "tfl_R_y_m": -0.38,
    "tfl_R_yaw_deg": -90.0,
    "tfl_B_x_m": -1.0,             # SENSOR 3 TF-Luna Rear: at the back, looking backwards
    "tfl_B_y_m": 0.0,
    "tfl_B_yaw_deg": 180.0,
    # obstacle-monitoring thresholds: clearance from the vehicle body (m). Research values, NOT certified limits.
    "prox_danger_m": 0.5,
    "prox_warning_m": 1.0,
    "prox_caution_m": 2.0,
    # alerts
    "alert_sound": True,           # soft chime in the app
    "alert_volume": 0.3,           # 0..1 (kept quiet: max gain is low)
    "alert_light": True,           # gentle glow in the app
    "mount_height_m": 0.6,         # height of the scan plane above the road (digital twin)
    # storage
    "log_interval_s": 1.0,         # one detection row per confirmed track per interval
    "retention_days": 30,
    "open_browser": True,
    "exit_when_unplugged": False,  # close the app when a lidar that was working disappears (off: works without it)
    "unplug_grace_s": 20.0,        # ...and stays gone this long (brief cable wiggles are tolerated)
}


class Config:
    def __init__(self):
        self._lock = threading.Lock()
        self.data = dict(DEFAULTS)
        if CONFIG_FILE.exists():
            try:
                self.data.update({k: v for k, v in json.loads(CONFIG_FILE.read_text()).items() if k in DEFAULTS})
            except (OSError, ValueError):
                pass

    def get(self) -> dict:
        with self._lock:
            return dict(self.data)

    def update(self, patch: dict) -> dict:
        with self._lock:
            for k, v in patch.items():
                if k not in DEFAULTS:
                    raise KeyError(k)
                # keep the type of the default (rejects e.g. strings for numbers)
                self.data[k] = type(DEFAULTS[k])(v) if not isinstance(DEFAULTS[k], bool) else bool(v)
            CONFIG_FILE.write_text(json.dumps(self.data, indent=2))
            return dict(self.data)

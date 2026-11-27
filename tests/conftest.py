import os
import tempfile

# Keep tests out of the real data/ folder; must run before backend.config is imported.
os.environ.setdefault("BANTAY_DATA", tempfile.mkdtemp(prefix="bantay-test-"))

# Tests must never open the real LiDAR or Pico (they may be plugged in): point both at a port that cannot exist.
import json  # noqa: E402
from pathlib import Path  # noqa: E402
(Path(os.environ["BANTAY_DATA"]) / "config.json").write_text(json.dumps(
    {"port": "TEST-NO-HARDWARE", "rf_port": "TEST-NO-HARDWARE", "open_browser": False}))

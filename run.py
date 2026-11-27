"""Start BANTAY LiDAR:  python run.py  [--port 8765] [--no-browser]"""
import argparse
import logging
import threading
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent
import os  # noqa: E402
DATA = Path(os.environ.get("BANTAY_DATA", ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)

p = argparse.ArgumentParser()
p.add_argument("--port", type=int, default=8765)
p.add_argument("--no-browser", action="store_true")
args = p.parse_args()

# Single instance: if BANTAY is already serving, just show it (a second copy could not open the port or COM6).
import json, sys, urllib.request  # noqa: E402
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/api/status", timeout=1.5) as r:
        if "lidar" in json.load(r):
            if not args.no_browser:
                webbrowser.open(f"http://127.0.0.1:{args.port}")
            print("BANTAY LiDAR is already running; opened it in the browser.")
            sys.exit(0)
except (OSError, ValueError):
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(),
                              RotatingFileHandler(DATA / "bantay.log", maxBytes=2_000_000, backupCount=3)])

if not (ROOT / "frontend" / "dist" / "index.html").exists():
    print("Frontend not built. Run:  cd frontend && npm install && npm run build")

threading.excepthook = lambda a: logging.getLogger("bantay").error(
    "thread %s crashed", a.thread.name if a.thread else "?", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
from backend.app import app, engine  # noqa: E402  (import after logging is configured)

url = f"http://127.0.0.1:{args.port}"
if engine.cfg.get()["open_browser"] and not args.no_browser:
    threading.Timer(1.5, webbrowser.open, (url,)).start()
print(f"BANTAY LiDAR running at {url}  (Ctrl+C to stop)")
uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")

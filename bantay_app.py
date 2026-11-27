"""BANTAY LiDAR desktop app: the same local server as run.py, shown in its own native window
(Windows WebView2 via pywebview) instead of a browser tab. Packaged to an .exe by build_app.ps1.

Closing the window stops scanning (sends the lidar its stop command) and exits.
"""
import json
import logging
import sys
import threading
import time
import urllib.request
from logging.handlers import RotatingFileHandler

PORT = 8765
URL = f"http://127.0.0.1:{PORT}"


def already_running() -> bool:
    try:
        with urllib.request.urlopen(URL + "/api/status", timeout=1.5) as r:
            return "lidar" in json.load(r)
    except (OSError, ValueError):
        return False


class WindowControls:
    """Called from the app's own title-bar buttons (the window is frameless, macOS-style)."""

    def __init__(self):
        self._win, self._maxed = None, False   # underscore: pywebview does not expose these to JS

    def minimize(self):
        self._win.minimize()

    def toggle_maximize(self):
        (self._win.restore if self._maxed else self._win.maximize)()
        self._maxed = not self._maxed

    def close(self):
        self._win.destroy()


TITLE = "PROJECT B.A.N.T.A.Y."
WINDOW = dict(width=1440, height=900, min_size=(960, 640), background_color="#1A1917", frameless=True, easy_drag=False)


def main():
    import webview

    if already_running():   # second launch: just show another window on the running instance
        ctl = WindowControls()
        ctl._win = webview.create_window(TITLE, URL, js_api=ctl, **WINDOW)
        webview.start()
        return

    from backend.config import DATA
    handlers = [RotatingFileHandler(DATA / "bantay.log", maxBytes=2_000_000, backupCount=3)]
    if sys.stderr:          # no console in the packaged app
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", handlers=handlers)

    import uvicorn
    threading.excepthook = lambda a: logging.getLogger("bantay").error(   # no console in the packaged app
        "thread %s crashed", a.thread.name if a.thread else "?", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    from backend.app import app, engine

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_config=None, log_level="warning"))
    threading.Thread(target=server.run, daemon=True, name="server").start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)

    ctl = WindowControls()
    window = webview.create_window(TITLE, URL, js_api=ctl, **WINDOW)
    ctl._win = window
    engine.on_exit = window.destroy   # lidar unplugged -> engine closes the window

    def closing():
        if engine._rec_file:            # finish the current recording cleanly
            engine._rec_file.close()
            engine._rec_file = None
        engine.driver.stop()
        engine.driver.join(timeout=2)   # lets the driver send the lidar its stop command (motor off)
        server.should_exit = True

    window.events.closed += closing
    webview.start()
    closing()


if __name__ == "__main__":
    main()

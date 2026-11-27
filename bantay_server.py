"""Headless BANTAY server for the Electron desktop app (desktop/main.js starts it, packaged by build_app.ps1).

Stops cleanly (recording closed, lidar motor off) when Electron exits, which closes our stdin, or when the
lidar stays unplugged (engine.shutdown -> on_exit). Electron quits when this process exits.
"""
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler

PORT = 8765


def main():
    from backend.config import DATA
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        handlers=[RotatingFileHandler(DATA / "bantay.log", maxBytes=2_000_000, backupCount=3)])
    threading.excepthook = lambda a: logging.getLogger("bantay").error(
        "thread %s crashed", a.thread.name if a.thread else "?", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))

    import uvicorn
    from backend.app import app, engine

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_config=None, log_level="warning"))
    engine.on_exit = lambda: setattr(server, "should_exit", True)

    def parent_gone():
        sys.stdin.read()                 # returns at EOF: the Electron window process has exited
        engine.shutdown()
        engine.driver.join(timeout=2)    # lets the driver send the lidar its stop command

    if sys.stdin:
        threading.Thread(target=parent_gone, daemon=True, name="parent-watch").start()
    server.run()


if __name__ == "__main__":
    main()

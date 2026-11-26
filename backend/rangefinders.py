"""Three single-beam lidar range finders on a Raspberry Pi Pico W (firmware: firmware/pico_rangefinders/main.py).

Link: the Pico's USB serial (MicroPython REPL port, VID 0x2E8A).
  Pico -> PC, ~20 Hz:  $RF,<ms>,<L_cm>,<R_cm>,<C_cm>,<L_str>,<R_str>,<C_str>*      (-1 = no reading)
  PC -> Pico:          !A,<L>,<R>,<S>                 levels 0 clear, 1 caution, 2 warning (LEDs + buzzer)

Beam directions are set in config (rf_bearing_*) relative to the T-mini Plus front, counter-clockwise positive.
The beams are assumed to start at the T-mini Plus position (shortcut: no per-sensor lever arm; add x/y offsets
to the config if the sensors are mounted far apart).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("bantay.rangefinders")
PICO_VID = 0x2E8A
SIDES = ("L", "R", "C")


def parse_line(line: str) -> dict | None:
    """Parse one '$RF,...*' frame; returns {'ms', 'L': (m|None, strength|None), ...} or None if malformed."""
    line = line.strip()
    if not (line.startswith("$RF,") and line.endswith("*")):
        return None
    parts = line[4:-1].split(",")
    if len(parts) != 7:
        return None
    try:
        ms, *vals = (int(p) for p in parts)
    except ValueError:
        return None
    out = {"ms": ms}
    for i, side in enumerate(SIDES):
        cm, strength = vals[i], vals[i + 3]
        out[side] = (cm / 100.0 if cm > 0 else None, strength if strength >= 0 else None)
    return out


@dataclass
class RFStatus:
    state: str = "disconnected"     # disconnected | connected | stale | error | disabled
    port: str | None = None
    message: str = "Pico W not found"
    rate_hz: float = 0.0
    last_t: float | None = None
    readings: dict = field(default_factory=lambda: {s: {"m": None, "strength": None} for s in SIDES})
    frames_bad: int = 0


class RangefinderLink(threading.Thread):
    def __init__(self, get_cfg):
        super().__init__(daemon=True, name="rangefinders")
        self.get_cfg = get_cfg
        self.status = RFStatus()
        self._halt = threading.Event()
        self._ser = None
        self._alert_sent: tuple | None = None
        self._alert_t = 0.0

    def stop(self):
        self._halt.set()

    def send_alert(self, levels: tuple[int, int, int]):
        """Forward alert levels to the Pico (LEDs/buzzer). Sent on change and as a 1 s keep-alive."""
        ser, now = self._ser, time.time()
        if ser is None or (levels == self._alert_sent and now - self._alert_t < 1.0):
            return
        try:
            ser.write(f"!A,{levels[0]},{levels[1]},{levels[2]}\n".encode())
            self._alert_sent, self._alert_t = levels, now
        except Exception:
            pass

    @staticmethod
    def find_port(cfg_port: str) -> str | None:
        from serial.tools import list_ports
        if cfg_port and cfg_port != "auto":
            return cfg_port
        picos = [p.device for p in list_ports.comports() if p.vid == PICO_VID]
        return picos[0] if picos else None

    def run(self):
        import serial
        st = self.status
        while not self._halt.is_set():
            cfg = self.get_cfg()
            if not cfg["rf_enabled"]:
                st.state, st.message = "disabled", "Range finders turned off in Settings"
                time.sleep(1)
                continue
            port = self.find_port(cfg["rf_port"])
            if not port:
                st.state, st.port, st.message = "disconnected", None, "Pico W not plugged in"
                time.sleep(2)
                continue
            try:
                with serial.Serial(port, 115200, timeout=0.2) as ser:
                    self._ser, st.port = ser, port
                    st.state, st.message = "stale", "Waiting for the Pico to send readings"
                    buf, n, t0 = b"", 0, time.time()
                    while not self._halt.is_set() and self.get_cfg()["rf_enabled"]:
                        buf += ser.read(ser.in_waiting or 1)
                        *lines, buf = buf.split(b"\n")
                        for raw in lines:
                            f = parse_line(raw.decode("ascii", "ignore"))
                            if f is None:
                                if raw.strip().startswith(b"$RF"):
                                    st.frames_bad += 1
                                continue
                            n += 1
                            st.last_t = time.time()
                            st.readings = {s: {"m": f[s][0], "strength": f[s][1]} for s in SIDES}
                        now = time.time()
                        if now - t0 >= 1:
                            st.rate_hz, n, t0 = n / (now - t0), 0, now
                        fresh = st.last_t is not None and now - st.last_t < 1.0
                        st.state = "connected" if fresh else "stale"
                        st.message = "Receiving range finder readings" if fresh else \
                            "Pico connected, but no readings (is the BANTAY firmware running?)"
            except Exception as e:   # unplugged, port busy, etc.
                st.state, st.message = "error", f"{type(e).__name__}: {e}"
                time.sleep(2)
            finally:
                self._ser = None

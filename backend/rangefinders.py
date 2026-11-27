"""Three Benewake TF-Luna sensors on a Raspberry Pi Pico W (firmware: firmware/pico_rangefinders/main.py v2).

The Pico is a separate USB serial device from the YDLIDAR; this link is independent of the LiDAR driver.
Pico -> PC (ASCII lines, XOR checksum of the text between '$' and '*'):
    $TFL,<seq>,<ms>,<id>,<pos>,<dist_cm>,<amp>,<status>*HH    per sensor, ~20 Hz
    $DIAG,<seq>,<ms>,<id>,<frames_ok>,<frames_bad>,<bytes>*HH   per sensor, 1 Hz
    $BOOT,<fw>,<id>:<pos>:<iface>:<pins>|...*HH                 at start, every 10 s
PC -> Pico:  !A,<left>,<right>,<status>   levels 0 safe, 1 caution, 2 warning, 3 danger

A distance is only ever shown while it is FRESH (< STALE_S old) and the sensor reported status OK.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("bantay.rangefinders")
PICO_VID = 0x2E8A
STALE_S = 0.5
# fixed identity: the id <-> position mapping is part of the wiring and must match (Left 1, Right 2, Rear 3)
SENSORS = {1: ("TF-Luna Left", "L"), 2: ("TF-Luna Right", "R"), 3: ("TF-Luna Rear", "B")}
POS_TO_ID = {pos: sid for sid, (_, pos) in SENSORS.items()}
PICO_STATUSES = {"OK", "WEAK", "STALE", "NODATA", "BADFRAMES"}


def checksum_ok(line: str) -> str | None:
    """Return the body of a '$body*HH' line if its XOR checksum matches, else None."""
    line = line.strip()
    if not line.startswith("$") or len(line) < 4 or line[-3] != "*":
        return None
    body, given = line[1:-3], line[-2:]
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    try:
        return body if cs == int(given, 16) else None
    except ValueError:
        return None


def parse_line(line: str) -> dict | None:
    """Validate and decode one Pico line. Returns None for anything malformed or inconsistent."""
    body = checksum_ok(line)
    if body is None:
        return None
    f = body.split(",")
    try:
        if f[0] == "TFL" and len(f) == 8:
            sid, pos, status = int(f[3]), f[4], f[7]
            if SENSORS.get(sid, (None, None))[1] != pos or status not in PICO_STATUSES:
                return None                                    # id/position mismatch or unknown status
            cm, amp = int(f[5]), int(f[6])
            return {"type": "TFL", "seq": int(f[1]), "ms": int(f[2]), "id": sid, "pos": pos,
                    "dist_m": cm / 100.0 if (status == "OK" and cm >= 0) else None,
                    "amp": amp if amp >= 0 else None, "status": status}
        if f[0] == "DIAG" and len(f) == 7:
            sid = int(f[3])
            if sid not in SENSORS:
                return None
            return {"type": "DIAG", "id": sid, "frames_ok": int(f[4]), "frames_bad": int(f[5]), "bytes": int(f[6])}
        if f[0] == "BOOT" and len(f) == 3:
            return {"type": "BOOT", "fw": f[1], "sensors": f[2]}
        if f[0] == "ERR":
            return {"type": "ERR", "text": ",".join(f[1:])}
    except (ValueError, IndexError):
        return None
    return None


@dataclass
class SensorState:
    id: int
    name: str
    pos: str
    dist_m: float | None = None       # last valid distance (shown only while fresh)
    amp: int | None = None
    status: str = "OFFLINE"           # Pico-reported status, or OFFLINE / SIMULATED
    last_rx_t: float | None = None    # last line received for this sensor
    last_ok_t: float | None = None    # last line with a valid distance
    seq: int | None = None
    frames_ok: int = 0
    frames_bad: int = 0
    bytes: int = 0
    source: str = "pico"              # pico | simulation

    def view(self, now: float) -> dict:
        fresh = self.last_rx_t is not None and now - self.last_rx_t < STALE_S
        live = fresh and self.dist_m is not None
        status = self.status if fresh else ("STALE" if self.last_rx_t else "OFFLINE")
        return {"id": self.id, "name": self.name, "pos": self.pos, "status": status, "fresh": fresh,
                "m": self.dist_m if live else None, "amp": self.amp if fresh else None,
                "age_s": None if self.last_rx_t is None else round(now - self.last_rx_t, 2),
                "last_ok_t": self.last_ok_t, "frames_ok": self.frames_ok, "frames_bad": self.frames_bad,
                "bytes": self.bytes, "source": self.source}


@dataclass
class LinkStatus:
    state: str = "disconnected"        # disconnected | connected | stale | error | disabled | simulation
    port: str | None = None
    message: str = "Pico W not plugged in"
    firmware: str | None = None
    rate_hz: float = 0.0
    lines_ok: int = 0
    lines_bad: int = 0
    errors: list = field(default_factory=list)
    last_t: float | None = None
    connects: int = 0


class RangefinderLink(threading.Thread):
    """Independent USB-serial connection to the Pico W. Reconnects by itself; never touches the LiDAR port."""

    def __init__(self, get_cfg):
        super().__init__(daemon=True, name="rangefinders")
        self.get_cfg = get_cfg
        self.status = LinkStatus()
        self.sensors = {sid: SensorState(sid, name, pos) for sid, (name, pos) in SENSORS.items()}
        self._halt = threading.Event()
        self._ser = None
        self._alert_sent: tuple | None = None
        self._alert_t = 0.0
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ public
    def stop(self):
        self._halt.set()

    def views(self, now=None) -> list[dict]:
        now = time.time() if now is None else now
        with self.lock:
            return [s.view(now) for s in self.sensors.values()]

    def readings(self, now=None) -> dict:
        """{pos: metres or None} for fresh, valid readings only."""
        return {v["pos"]: v["m"] for v in self.views(now)}

    def inject_simulated(self, readings: dict, now=None):
        """Simulation source: digital-twin ray casts, marked SIMULATED (never reported as Pico data)."""
        now = time.time() if now is None else now
        with self.lock:
            for pos, m in readings.items():
                s = self.sensors[POS_TO_ID[pos]]
                s.dist_m, s.amp, s.status, s.source = m, None, "SIMULATED", "simulation"
                s.last_rx_t = now
                if m is not None:
                    s.last_ok_t = now

    def handle_line(self, raw: str, now=None) -> bool:
        """Apply one received line. Returns True if it was valid. Public so the parser can be tested directly."""
        now = time.time() if now is None else now
        msg = parse_line(raw)
        st = self.status
        if msg is None:
            if raw.strip():
                st.lines_bad += 1
            return False
        st.lines_ok += 1
        st.last_t = now
        with self.lock:
            if msg["type"] == "TFL":
                s = self.sensors[msg["id"]]
                if s.seq is not None and msg["seq"] == s.seq:
                    return True                                     # duplicate line
                s.seq, s.status, s.amp, s.source = msg["seq"], msg["status"], msg["amp"], "pico"
                s.dist_m = msg["dist_m"]
                s.last_rx_t = now
                if msg["dist_m"] is not None:
                    s.last_ok_t = now
            elif msg["type"] == "DIAG":
                s = self.sensors[msg["id"]]
                s.frames_ok, s.frames_bad, s.bytes = msg["frames_ok"], msg["frames_bad"], msg["bytes"]
            elif msg["type"] == "BOOT":
                st.firmware = msg["fw"]
            elif msg["type"] == "ERR":
                st.errors = (st.errors + [msg["text"]])[-5:]
                log.warning("Pico reported: %s", msg["text"])
        return True

    def send_alert(self, levels: tuple[int, int, int]):
        """Forward alert levels (0..3) to the Pico. Sent on change and as a 1 s keep-alive."""
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

    # ------------------------------------------------------------------ thread
    def run(self):
        import serial
        st = self.status
        while not self._halt.is_set():
            cfg = self.get_cfg()
            if cfg["source"] == "simulation":
                st.state, st.message = "simulation", "Simulation: TF-Luna readings come from the digital twin"
                time.sleep(0.5)
                continue
            if not cfg["rf_enabled"]:
                st.state, st.message = "disabled", "TF-Luna sensors turned off in Settings"
                time.sleep(1)
                continue
            port = self.find_port(cfg["rf_port"])
            if not port:
                st.state, st.port, st.message = "disconnected", None, "Pico W not plugged in"
                time.sleep(1)
                continue
            try:
                ser = serial.serial_for_url(port, baudrate=115200, timeout=0.1)
            except Exception as e:
                st.state, st.message = "error", f"Cannot open {port}: {e}"
                log.warning("pico: %s", st.message)
                time.sleep(2)
                continue
            st.connects += 1
            self._ser, st.port = ser, port
            st.state, st.message = "stale", "Connected; waiting for TF-Luna lines (is BANTAY firmware v2 on the Pico?)"
            buf, n, t0 = b"", 0, time.time()
            try:
                while not self._halt.is_set():
                    c = self.get_cfg()
                    if not c["rf_enabled"] or c["source"] == "simulation" or c["rf_port"] not in ("auto", port):
                        break
                    buf += ser.read(ser.in_waiting or 1)
                    *lines, buf = buf.split(b"\n")
                    if len(buf) > 4096:
                        buf = b""                                       # garbage without newlines
                    for raw in lines:
                        if self.handle_line(raw.decode("ascii", "replace")):
                            n += 1
                    now = time.time()
                    if now - t0 >= 1:
                        st.rate_hz, n, t0 = n / (now - t0), 0, now
                    fresh = st.last_t is not None and now - st.last_t < 1.0
                    st.state = "connected" if fresh else "stale"
                    if fresh:
                        st.message = f"Receiving · firmware {st.firmware or '?'}"
            except Exception as e:                                       # unplugged mid-stream, etc.
                st.state, st.message = "error", f"Lost the Pico: {type(e).__name__}"
                log.warning("pico: %s", e)
            finally:
                self._ser = None
                try:
                    ser.close()
                except Exception:
                    pass
            time.sleep(0.5)

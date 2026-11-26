"""YDLIDAR T-mini Plus serial protocol and driver.

Every byte layout here comes from the "YDLIDAR T-mini Plus Development Manual" v1.0
(EAI, 2023-12-21) and was cross-checked with the official YDLidar-SDK (src/YDlidarDriver.cpp).
See docs/HARDWARE.md for the section references.
"""
from __future__ import annotations

import logging
import struct
import threading
import time
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger("bantay.lidar")

SYNC = 0xA5
CMD_SCAN, CMD_STOP, CMD_INFO, CMD_HEALTH = 0x60, 0x65, 0x90, 0x92
CMD_FREQ_UP_1, CMD_FREQ_DOWN_1, CMD_FREQ_GET = 0x0B, 0x0C, 0x0D
CMD_FREQ_UP_01, CMD_FREQ_DOWN_01 = 0x09, 0x0A
HEADER = b"\xaa\x55"
HEAD_SIZE = 10          # PH(2) CT(1) LSN(1) FSA(2) LSA(2) CS(2)
SAMPLE_SIZE = 3         # intensity(1) + distance/flag(2), manual 3.1.2-3.1.4
MAX_LSN = 80            # SDK TRI_PACKMAXNODES


@dataclass
class Packet:
    zero: bool           # CT bit0: start of a revolution
    ct: int
    angles: np.ndarray   # degrees, clockwise as reported by the sensor (manual 3.1.5)
    dist_mm: np.ndarray  # 0 = invalid (manual section 5, note 3)
    intensity: np.ndarray
    flag: np.ndarray     # 2 = specular interference, 3 = ambient light (manual 3.1.3)


def checksum(buf: bytes) -> int:
    """16-bit XOR from manual 3.1.6: header words, then each sample as (0x00,intensity) and (dist word)."""
    w = struct.unpack_from("<4H", buf, 0)
    cs = w[0] ^ w[1] ^ w[2] ^ w[3]
    for i in range(buf[3]):
        o = HEAD_SIZE + i * SAMPLE_SIZE
        cs ^= buf[o]
        cs ^= buf[o + 1] | (buf[o + 2] << 8)
    return cs


def decode_packet(buf: bytes) -> Packet:
    ct, lsn = buf[2], buf[3]
    fsa, lsa, _ = struct.unpack_from("<3H", buf, 4)
    a0, a1 = (fsa >> 1) / 64.0, (lsa >> 1) / 64.0
    diff = (a1 - a0) % 360.0            # clockwise difference, wraps through 0
    s = np.frombuffer(buf, dtype=np.uint8, count=lsn * 3, offset=HEAD_SIZE).reshape(lsn, 3)
    word = s[:, 1].astype(np.uint16) | (s[:, 2].astype(np.uint16) << 8)
    step = diff / (lsn - 1) if lsn > 1 else 0.0
    angles = (a0 + step * np.arange(lsn)) % 360.0
    return Packet(bool(ct & 1), ct, angles, (word >> 2).astype(np.float32),
                  s[:, 0].copy(), (word & 0x3).astype(np.uint8))


class PacketParser:
    """Incremental byte-stream parser. Resyncs on AA 55; drops packets with a bad checksum or check bit."""

    def __init__(self):
        self.buf = bytearray()
        self.ok = 0
        self.bad = 0

    def feed(self, data: bytes) -> list[Packet]:
        self.buf += data
        out = []
        while True:
            i = self.buf.find(HEADER)
            if i < 0:
                del self.buf[:-1]   # keep a trailing 0xAA that might start a header
                return out
            if i:
                del self.buf[:i]
            if len(self.buf) < HEAD_SIZE:
                return out
            lsn = self.buf[3]
            # FSA/LSA bit0 is the fixed check bit "C" (manual fig. 7)
            if lsn == 0 or lsn > MAX_LSN or not (self.buf[4] & 1 and self.buf[6] & 1):
                self.bad += 1
                del self.buf[:2]
                continue
            n = HEAD_SIZE + lsn * SAMPLE_SIZE
            if len(self.buf) < n:
                return out
            pkt = bytes(self.buf[:n])
            if checksum(pkt) != struct.unpack_from("<H", pkt, 8)[0]:
                self.bad += 1
                del self.buf[:2]
                continue
            del self.buf[:n]
            self.ok += 1
            out.append(decode_packet(pkt))


@dataclass
class Scan:
    """One full 360-degree revolution of original measurements (unaltered)."""
    t: float
    angles: np.ndarray
    dist_mm: np.ndarray
    intensity: np.ndarray
    flag: np.ndarray
    freq_hz: float | None = None   # sensor-reported, from the zero packet CT (manual chart 5)

    def to_json(self) -> dict:
        return {"t": self.t, "freq": self.freq_hz,
                "a": np.round(self.angles, 3).tolist(), "d": self.dist_mm.astype(int).tolist(),
                "i": self.intensity.astype(int).tolist(), "f": self.flag.astype(int).tolist()}

    @staticmethod
    def from_json(o: dict) -> "Scan":
        return Scan(o["t"], np.array(o["a"], np.float64), np.array(o["d"], np.float32),
                    np.array(o["i"], np.uint8), np.array(o["f"], np.uint8), o.get("freq"))


class ScanAssembler:
    """Groups packets into revolutions. A new revolution starts on a zero packet, or on an angle
    wrap-around if a zero packet was lost to a checksum failure."""

    def __init__(self):
        self.parts: list[Packet] = []
        self.freq: float | None = None
        self.last_angle = None

    def push(self, p: Packet) -> Scan | None:
        wrapped = self.last_angle is not None and len(p.angles) and p.angles[0] + 180 < self.last_angle
        done = None
        if (p.zero or wrapped) and self.parts:
            done = Scan(time.time(), *(np.concatenate([getattr(q, k) for q in self.parts])
                                       for k in ("angles", "dist_mm", "intensity", "flag")), self.freq)
            self.parts = []
        if p.zero:
            self.freq = (p.ct >> 1) / 10.0
        self.parts.append(p)
        if len(p.angles):
            self.last_angle = p.angles[-1]
        return done


def cmd(c: int) -> bytes:
    return bytes((SYNC, c))


@dataclass
class LidarStatus:
    state: str = "disconnected"   # disconnected | connecting | scanning | error | replay
    port: str | None = None
    message: str = "Not connected"
    model: int | None = None
    firmware: str | None = None
    hardware: int | None = None
    serial: str | None = None
    health: int | None = None
    freq_reported: float | None = None
    freq_measured: float | None = None
    bytes_per_s: float = 0.0
    packets_ok: int = 0
    packets_bad: int = 0
    scans: int = 0
    last_scan_t: float | None = None
    extra: dict = field(default_factory=dict)


class LidarDriver(threading.Thread):
    """Owns the serial port in its own thread so visualization can never block acquisition.
    Reconnects automatically. Calls on_scan(Scan) for every complete revolution."""

    def __init__(self, get_cfg, on_scan):
        super().__init__(daemon=True, name="lidar")
        self.get_cfg, self.on_scan = get_cfg, on_scan
        self.status = LidarStatus()
        self._halt = threading.Event()
        self._restart = threading.Event()
        self._freq_request: float | None = None

    def restart(self):
        self._restart.set()

    def request_frequency(self, hz: float):
        self._freq_request = hz
        self._restart.set()

    def stop(self):
        self._halt.set()
        self._restart.set()

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _read_response(ser, timeout=1.0) -> tuple[int, bytes] | None:
        """Read one A5 5A single response (manual 2.3): returns (type_code, content)."""
        end, buf = time.time() + timeout, bytearray()
        while time.time() < end:
            buf += ser.read(ser.in_waiting or 1)
            i = buf.find(b"\xa5\x5a")
            if i >= 0 and len(buf) >= i + 7:
                size = struct.unpack_from("<I", buf, i + 2)[0] & 0x3FFFFFFF
                typ = buf[i + 6]
                while len(buf) < i + 7 + size and time.time() < end:
                    buf += ser.read(ser.in_waiting or 1)
                return typ, bytes(buf[i + 7:i + 7 + size])
        return None

    def _query(self, ser):
        st = self.status
        ser.reset_input_buffer()
        ser.write(cmd(CMD_INFO))
        r = self._read_response(ser)
        if r and r[0] == 0x04 and len(r[1]) >= 20:
            c = r[1]
            st.model, st.firmware, st.hardware = c[0], f"{c[1]}.{c[2]}", c[3]
            st.serial = c[4:20].hex()
        ser.write(cmd(CMD_HEALTH))
        r = self._read_response(ser)
        if r and r[0] == 0x06 and len(r[1]) >= 1:
            st.health = r[1][0]

    def _set_frequency(self, ser, target: float):
        """Step the scan frequency in idle mode (manual 3.5; commands are not accepted while scanning)."""
        for _ in range(80):
            ser.write(cmd(CMD_FREQ_GET))
            r = self._read_response(ser)
            if not r or len(r[1]) < 4:
                log.warning("frequency query got no response")
                return
            cur = struct.unpack_from("<I", r[1])[0] / 100.0
            self.status.freq_reported = cur
            d = target - cur
            if abs(d) < 0.05:
                return
            c = (CMD_FREQ_UP_1 if d > 0 else CMD_FREQ_DOWN_1) if abs(d) >= 1 else \
                (CMD_FREQ_UP_01 if d > 0 else CMD_FREQ_DOWN_01)
            ser.write(cmd(c))
            self._read_response(ser, 0.5)

    @staticmethod
    def pick_port(cfg_port: str) -> str | None:
        from serial.tools import list_ports
        if cfg_port and cfg_port != "auto":
            return cfg_port
        # Pico (VID 0x2E8A) is never the lidar in the direct-USB architecture.
        ports = [p for p in list_ports.comports() if p.vid is not None and p.vid != 0x2E8A]
        return ports[0].device if len(ports) == 1 else None

    # -------------------------------------------------------------- main loop
    def run(self):
        import serial
        st = self.status
        while not self._halt.is_set():
            cfg = self.get_cfg()
            if cfg["source"] != "serial":
                st.state, st.message = "disconnected", "Serial acquisition disabled (source set to replay)"
                self._restart.wait(1.0)
                self._restart.clear()
                continue
            port = self.pick_port(cfg["port"])
            if not port:
                st.state, st.port = "disconnected", None
                st.message = "No lidar port found. Plug in the USB adapter or choose a port in Hardware."
                self._restart.wait(2.0)
                self._restart.clear()
                continue
            st.state, st.port, st.message = "connecting", port, f"Opening {port}"
            try:
                with serial.Serial(port, cfg["baud"], bytesize=8, parity="N", stopbits=1, timeout=0.1) as ser:
                    ser.write(cmd(CMD_STOP))
                    time.sleep(0.1)
                    self._query(ser)
                    if self._freq_request:
                        self._set_frequency(ser, self._freq_request)
                        self._freq_request = None
                    ser.reset_input_buffer()
                    ser.write(cmd(CMD_SCAN))   # the A5 5A ... 81 descriptor is skipped by the AA 55 resync
                    self._stream(ser, st)
                    try:
                        ser.write(cmd(CMD_STOP))
                    except Exception:
                        pass
            except Exception as e:  # serial errors: unplugged, access denied, etc.
                st.state, st.message = "error", f"{type(e).__name__}: {e}"
                log.warning("lidar: %s", st.message)
                self._restart.wait(2.0)
            self._restart.clear()

    def _stream(self, ser, st):
        parser, asm = PacketParser(), ScanAssembler()
        st.state, st.message = "connecting", "Waiting for scan data"
        t0, nbytes, last_scan = time.time(), 0, None
        started = t0
        while not self._restart.is_set():
            data = ser.read(ser.in_waiting or 1)
            nbytes += len(data)
            for p in parser.feed(data):
                scan = asm.push(p)
                if scan is None:
                    continue
                dt = scan.t - last_scan if last_scan else 0.0
                # two revolutions can finish in one serial read and share a timestamp: skip those
                if dt > 1e-3:
                    st.freq_measured = 1 / dt if st.freq_measured is None else 0.8 * st.freq_measured + 0.2 / dt
                last_scan = scan.t
                st.state, st.message = "scanning", "Receiving scans"
                st.scans += 1
                st.last_scan_t = scan.t
                st.freq_reported = scan.freq_hz
                self.on_scan(scan)
            st.packets_ok, st.packets_bad = parser.ok, parser.bad
            now = time.time()
            if now - t0 >= 1:
                st.bytes_per_s, t0, nbytes = nbytes / (now - t0), now, 0
            if now - (last_scan or started) > 5:
                raise TimeoutError("No complete scan for 5 s (check power: the T-mini Plus needs 5 V)")

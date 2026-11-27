"""TF-Luna x3 on the Pico W + vehicle obstacle monitoring.

The firmware's protocol/parsing logic is imported and tested here on the PC (no Pico needed). Serial tests use
pyserial's loop:// device. Nothing in these tests is presented as a real hardware result."""
import importlib.util
import math
import time
from pathlib import Path

import pytest

from backend import vehicle
from backend.config import DEFAULTS
from backend.rangefinders import STALE_S, RangefinderLink, checksum_ok, parse_line

ROOT = Path(__file__).resolve().parent.parent
CFG = dict(DEFAULTS, angle_offset_deg=90.0)        # vehicle front points up the plot, as on the user's unit


def firmware():
    spec = importlib.util.spec_from_file_location("bantay_fw", ROOT / "firmware" / "pico_rangefinders" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                    # no rp2/machine on a PC: main() is not run
    return mod


def frame(cm, amp, temp=0):
    f = bytearray([0x59, 0x59, cm & 0xFF, cm >> 8, amp & 0xFF, amp >> 8, temp & 0xFF, temp >> 8])
    f.append(sum(f) & 0xFF)
    return bytes(f)


# ----------------------------------------------------------------------------- firmware logic
def test_firmware_sensor_map_matches_reported_wiring():
    fw = firmware()
    assert fw.SENSORS == ((1, "TF-Luna Left", "L", "UART0", "GP17"),
                          (2, "TF-Luna Right", "R", "UART1", "GP9"),
                          (3, "TF-Luna Rear", "B", "I2C0", "GP4/GP5"))


def test_firmware_i2c_polled_path():
    fw = firmware()
    p = fw.TFLunaParser()
    p.set_polled(None, None, 0)                            # sensor did not answer
    assert p.reading(0)[2] == "NODATA" and p.frames_bad == 1
    p.set_polled(41, 900, 10)
    assert p.reading(10) == (41, 900, "OK")
    assert p.reading(10 + fw.STALE_MS + 1)[2] == "STALE"


def test_firmware_parser_valid_weak_stale_and_corrupt():
    fw = firmware()
    p = fw.TFLunaParser()
    assert p.reading(0)[2] == "NODATA"
    p.feed(b"\x00\x12" + frame(234, 800)[:5], 0)          # junk + half a frame
    p.feed(frame(234, 800)[5:], 10)
    assert p.reading(10) == (234, 800, "OK")
    bad = bytearray(frame(100, 800)); bad[8] ^= 0xFF      # checksum error must not change the reading
    p.feed(bytes(bad), 20)
    assert p.reading(20) == (234, 800, "OK") and p.frames_bad >= 1
    p.feed(frame(50, 40), 30)                              # amplitude < 100: unreliable
    assert p.reading(30) == (-1, 40, "WEAK")
    assert p.reading(30 + fw.STALE_MS + 1)[2] == "STALE"   # never report an old value as live
    q = fw.TFLunaParser()
    q.feed(bytes(range(40)), 0)                            # bytes but never a valid frame: wrong baud/format
    assert q.reading(0)[2] == "BADFRAMES"


def test_firmware_lines_are_accepted_by_the_app():
    fw = firmware()
    for body in ("TFL,7,1234,1,L,234,800,OK", "TFL,7,1234,3,B,-1,-1,STALE", "DIAG,7,1234,2,10,1,99"):
        msg = parse_line(fw.line(body))
        assert msg is not None, body
    assert checksum_ok(fw.boot_line()) and parse_line(fw.boot_line())["fw"] == fw.FW_VERSION
    assert fw.parse_alert("!A,1,2,9") == (1, 2, 3) and fw.parse_alert("!A,x,1,1") is None
    assert fw.led_on(0, 123) == 0 and fw.buzzer_on(0, 0) is False


# ----------------------------------------------------------------------------- app-side protocol
def test_app_rejects_bad_packets():
    fw = firmware()
    good = fw.line("TFL,1,100,2,R,150,500,OK")
    assert parse_line(good)["dist_m"] == 1.5
    assert parse_line(good[:-1] + ("0" if good[-1] != "0" else "1")) is None      # checksum
    assert parse_line(fw.line("TFL,1,100,1,R,150,500,OK")) is None                 # id 1 is LEFT, not R
    assert parse_line(fw.line("TFL,1,100,2,R,150,500,MAYBE")) is None             # unknown status
    assert parse_line(fw.line("TFL,1,100,2,R,150")) is None                       # truncated
    assert parse_line("$TFL,1,100,2,R,150,500,OK") is None                        # no checksum at all
    assert parse_line(fw.line("TFL,1,100,2,R,150,500,WEAK"))["dist_m"] is None    # WEAK never gives a distance


def test_link_state_freshness_and_missing_sensors():
    fw = firmware()
    link = RangefinderLink(lambda: CFG)
    t = 1000.0
    assert link.handle_line(fw.line("TFL,1,100,1,L,120,900,OK"), t)
    assert not link.handle_line("garbage", t) and link.status.lines_bad == 1
    v = {s["pos"]: s for s in link.views(t + 0.1)}
    assert v["L"]["m"] == 1.2 and v["L"]["status"] == "OK" and v["L"]["fresh"]
    assert v["R"]["status"] == "OFFLINE" and v["R"]["m"] is None                 # missing sensor: no crash, no value
    assert v["B"]["status"] == "OFFLINE"
    stale = {s["pos"]: s for s in link.views(t + STALE_S + 0.1)}["L"]
    assert stale["m"] is None and stale["status"] == "STALE"                     # old distance never shown as live


def test_link_over_loopback_serial_with_reconnect():
    fw = firmware()
    cfg = dict(CFG, rf_port="loop://", source="serial", rf_enabled=True)
    link = RangefinderLink(lambda: cfg)
    link.start()
    try:
        wait = lambda cond, s=5: next((True for _ in range(int(s / 0.05)) if cond() or time.sleep(0.05)), False)
        assert wait(lambda: link._ser is not None)
        for body in ("BOOT,2.0,1:L:UART0:GP17|2:R:PIO0:GP9|3:B:UART1:GP4/GP5",
                     "TFL,1,100,1,L,80,700,OK", "TFL,1,100,2,R,300,700,OK", "TFL,1,100,3,B,-1,-1,NODATA"):
            link._ser.write((fw.line(body) + "\n").encode())
        link._ser.write(b"$TFL,corrupted*00\n")
        assert wait(lambda: {s["pos"]: s["m"] for s in link.views()} == {"L": 0.8, "R": 3.0, "B": None})
        assert link.status.firmware == "2.0" and link.status.lines_bad >= 1 and link.status.connects == 1
        link._ser.close()                                                       # simulate an unplug
        assert wait(lambda: link.status.connects == 2), "link did not reconnect by itself"
    finally:
        link.stop()
        link.join(timeout=5)


# ----------------------------------------------------------------------------- vehicle geometry + monitoring
def test_frames_are_inverse_and_sensors_sit_where_mounted():
    for v in ((1.0, 0.0), (0.3, -0.7), (-2.0, 1.5)):
        assert all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(vehicle.to_vehicle(*vehicle.to_plot(*v, CFG), CFG), v))
    g = vehicle.sensor_geometry(CFG)                     # front = up (+y): left is -x, rear is -y
    assert g["L"]["x"] < -0.3 and abs(g["L"]["y"]) < 1e-9 and g["L"]["angle"] % 360 == 180
    assert g["R"]["x"] > 0.3 and g["R"]["angle"] % 360 == 0
    assert g["B"]["y"] < -0.9 and g["B"]["angle"] % 360 == 270


def test_monitor_levels_sides_and_no_double_counting():
    left_pt = vehicle.to_plot(0.0, 0.375 + 0.8, CFG)     # 0.8 m from the left side of the body
    mon = vehicle.monitor(CFG, [(*left_pt, 2)], {"L": 0.9, "R": 0.4, "B": None})
    s = mon["sides"]
    assert s["left"]["nearest_m"] == pytest.approx(0.8) and s["left"]["source"] == "YDLIDAR"   # one value, not two
    assert s["left"]["label"] == "WARNING" and s["right"]["label"] == "DANGER" and s["rear"]["label"] == "SAFE"
    assert mon["overall_label"] == "DANGER"
    assert vehicle.monitor(CFG, None, {})["overall"] == 0                         # stale LiDAR: no warnings from it
    bg = vehicle.monitor(CFG, [(*left_pt, 0)], {})                                # static background point ignored
    assert bg["sides"]["left"]["nearest_m"] is None


def test_engine_alert_uses_monitor_and_simulated_tfluna_is_labelled():
    from backend.engine import Engine
    e = Engine()
    a = e.compute_alert(CFG, None, {"L": 0.3, "R": 1.8, "B": 0.9}, 100.0)
    assert (a["L"], a["R"], a["S"]) == (3, 1, 3)
    e.cfg.update({"source": "simulation", "angle_offset_deg": 90.0})
    e.start()
    deadline = time.time() + 20
    while time.time() < deadline and not all(v["source"] == "simulation" for v in e.rf.views()):
        time.sleep(0.2)
    views = e.rf.views()
    assert all(v["source"] == "simulation" and v["status"] == "SIMULATED" for v in views)
    rf = e.rf_frame()
    assert {s["pos"] for s in rf["sensors"]} == {"L", "R", "B"} and rf["vehicle"]["calibrated"] is False
    e.cfg.update({"source": "replay"})

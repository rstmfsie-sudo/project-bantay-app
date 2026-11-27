# PROJECT B.A.N.T.A.Y. - Raspberry Pi Pico W firmware (MicroPython 1.22+)            version 2.0
# Reads THREE Benewake TF-Luna sensors (Left / Right / Rear), streams structured, checksummed readings
# to the PC app over USB serial, and drives warning LEDs + a quiet buzzer from levels the app sends back.
#
# WIRING (confirmed from the firmware that was on the Pico on 2026-11-27, backed up in firmware/pico_backup/;
# all three sensors answered on these pins in a live test):
#   SENSOR 1  TF-Luna LEFT   UART mode   Pico GP16 (UART0 TX) -> TF-Luna RX,  TF-Luna TX -> GP17 (UART0 RX)
#   SENSOR 2  TF-Luna RIGHT  UART mode   Pico GP8  (UART1 TX) -> TF-Luna RX,  TF-Luna TX -> GP9  (UART1 RX)
#   SENSOR 3  TF-Luna REAR   I2C mode    GP4 = SDA, GP5 = SCL (I2C0), address 0x10 (TF-Luna pin 5 grounded = I2C)
#   Each sensor has its own peripheral (UART0, UART1, I2C0), so there is no pin or address conflict.
#   All TF-Luna: 5 V + GND.
#
# OTHER PINS (firmware choice, change freely): LEDs GP18 left, GP19 right, GP20 status (through resistors;
#   12 V lamps need a transistor); buzzer GP15.
#
# TF-Luna UART FRAME (Benewake manual; 115200 8N1 default): 9 bytes
#   0x59 0x59 Dist_L Dist_H Amp_L Amp_H Temp_L Temp_H Checksum(low byte of sum of bytes 0..7); distance in cm.
#   Amp < 100 or Amp == 0xFFFF -> distance unreliable (reported as WEAK, never as a valid distance).
# TF-Luna I2C (rear): registers 0x00/0x01 = distance (cm, little-endian), 0x02/0x03 = signal amplitude.
#
# PROTOCOL TO THE APP (ASCII lines, NMEA-style checksum = XOR of the characters between '$' and '*'):
#   $BOOT,<fw>,<id>:<pos>:<iface>:<pins>|...*HH                  at start and every 10 s
#   $TFL,<seq>,<ms>,<id>,<pos>,<dist_cm>,<amp>,<status>*HH          one line per sensor, ~20 Hz
#       id 1/2/3, pos L/R/B (rear), dist_cm -1 if invalid, amp -1 if none
#       status OK | WEAK | STALE (no valid frame for 300 ms) | NODATA (no byte ever) | BADFRAMES (bytes, no valid frame)
#   $DIAG,<seq>,<ms>,<id>,<frames_ok>,<frames_bad>,<bytes>*HH      once per second per sensor
# NO INPUT FROM THE APP: the Pico computes its own warning LEDs / buzzer from its TF-Luna distances (below), so
# it keeps warning even without the PC. The app never writes to the Pico: data the firmware does not read would
# fill the USB receive buffer, block the PC's write, and stop Ctrl-C from reaching the REPL (Thonny / mpremote).
#
# STATUS: protocol/parsing logic is unit-tested on a PC and compiles with mpy-cross. Uploaded to the Pico on
# 2026-11-27; its live output was not yet observed (devices were unplugged right after the upload).
# Install: close the BANTAY app, open Thonny, File > Save as > Raspberry Pi Pico > main.py, reset the Pico.

FW_VERSION = "2.1"
FRAME_LEN = 9
STALE_MS = 300
PERIOD_MS = 50
DIAG_MS = 1000
BOOT_MS = 10000
# warning levels from a sensor's own distance (cm): DANGER / WARNING / CAUTION. Research values, not certified;
# the same defaults as the app's Settings (prox_danger_m / prox_warning_m / prox_caution_m).
DANGER_CM, WARNING_CM, CAUTION_CM = 50, 100, 200
BUZZER_VOLUME = 1500            # PWM duty 0..65535 (~2 %): quiet on a passive piezo. 0 = silent
BUZZER_HZ = 2400

# id, name, position, interface, pins  (positions: L left, R right, B rear/back)
SENSORS = (
    (1, "TF-Luna Left", "L", "UART0", "GP17"),
    (2, "TF-Luna Right", "R", "UART1", "GP9"),
    (3, "TF-Luna Rear", "B", "I2C0", "GP4/GP5"),
)


# ------------------------------------------------------------------ pure logic (unit-tested on a PC)
def nmea_checksum(body):
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return "%02X" % cs


def line(body):
    return "$%s*%s" % (body, nmea_checksum(body))


class TFLunaParser:
    """Incremental TF-Luna frame parser for one sensor. Never reports an old or unreliable value as valid."""

    def __init__(self):
        self.buf = bytearray()
        self.cm = -1
        self.amp = -1
        self.valid_ms = None        # time of the last frame with a checksum match
        self.frames_ok = 0
        self.frames_bad = 0
        self.bytes = 0

    def feed(self, data, now_ms):
        self.bytes += len(data)
        self.buf.extend(data)
        while len(self.buf) >= FRAME_LEN:
            if self.buf[0] != 0x59 or self.buf[1] != 0x59:
                self.buf = self.buf[1:]
                continue
            f = self.buf[:FRAME_LEN]
            if (sum(f[:8]) & 0xFF) == f[8]:
                self.cm = f[2] | (f[3] << 8)
                self.amp = f[4] | (f[5] << 8)
                self.valid_ms = now_ms
                self.frames_ok += 1
                self.buf = self.buf[FRAME_LEN:]
            else:
                self.frames_bad += 1
                self.buf = self.buf[1:]
        if len(self.buf) > 64:
            self.buf = self.buf[-FRAME_LEN:]

    def set_polled(self, cm, amp, now_ms):
        """I2C path: one register read. None means the read failed (counted as a bad frame)."""
        if cm is None:
            self.frames_bad += 1
            return
        self.cm, self.amp, self.valid_ms = cm, amp, now_ms
        self.frames_ok += 1
        self.bytes += 4

    def reading(self, now_ms, ticks_diff=lambda a, b: a - b):
        """(dist_cm, amp, status) for the most recent state."""
        if self.valid_ms is None:
            return -1, -1, ("BADFRAMES" if self.bytes >= 2 * FRAME_LEN else "NODATA")
        if ticks_diff(now_ms, self.valid_ms) > STALE_MS:
            return -1, -1, "STALE"
        if self.amp < 100 or self.amp == 0xFFFF:
            return -1, self.amp, "WEAK"
        return self.cm, self.amp, "OK"


def level_for(cm, status):
    """0 safe, 1 caution, 2 warning, 3 danger from one TF-Luna reading; only a valid, fresh reading can warn."""
    if status != "OK" or cm < 0:
        return 0
    if cm <= DANGER_CM:
        return 3
    if cm <= WARNING_CM:
        return 2
    if cm <= CAUTION_CM:
        return 1
    return 0


def boot_line():
    return line("BOOT,%s,%s" % (FW_VERSION, "|".join("%d:%s:%s:%s" % (i, p, iface, pins) for i, _, p, iface, pins in SENSORS)))


def led_on(level, now_ms):
    """Blink pattern: off safe, 2 Hz caution, 4 Hz warning, 8 Hz danger."""
    if level <= 0:
        return 0
    return (now_ms // (250, 125, 62)[min(level, 3) - 1]) % 2


def buzzer_on(level, now_ms):
    """Soft beep once a second (caution), twice a second (warning), quick triple (danger)."""
    if level == 1:
        return now_ms % 1000 < 60
    if level == 2:
        return now_ms % 500 < 60
    if level >= 3:
        p = now_ms % 600
        return p < 50 or 100 <= p < 150 or 200 <= p < 250
    return False


# ------------------------------------------------------------------ hardware (only runs on the Pico)
def main():
    import time
    from machine import I2C, Pin, UART, PWM

    class HwUart:
        def __init__(self, uid, tx, rx):
            self.u = UART(uid, baudrate=115200, bits=8, parity=None, stop=1, tx=Pin(tx), rx=Pin(rx), rxbuf=512)

        def read_all(self):
            n = self.u.any()
            return self.u.read(n) if n else b""

    class TFLunaI2C:
        ADDR = 0x10

        def __init__(self, sda, scl):
            self.i2c = I2C(0, sda=Pin(sda), scl=Pin(scl), freq=400000)

        def poll(self):
            """(distance_cm, amplitude) or (None, None) if the sensor did not answer."""
            try:
                d = self.i2c.readfrom_mem(self.ADDR, 0x00, 4)
                return d[0] | (d[1] << 8), d[2] | (d[3] << 8)
            except OSError:
                return None, None

    print(boot_line())
    sources = {}
    for sid, iface, make in ((1, "UART0", lambda: HwUart(0, 16, 17)),
                             (2, "UART1", lambda: HwUart(1, 8, 9)),
                             (3, "I2C0", lambda: TFLunaI2C(4, 5))):
        try:
            sources[sid] = make()
        except Exception as e:           # one bad interface must not stop the other sensors
            print(line("ERR,init,%d,%s" % (sid, str(e).replace(",", ";"))))
    parsers = {s[0]: TFLunaParser() for s in SENSORS}

    leds = {"L": Pin(18, Pin.OUT), "R": Pin(19, Pin.OUT), "S": Pin(20, Pin.OUT)}
    buzzer = PWM(Pin(15))
    buzzer.freq(BUZZER_HZ)
    buzzer.duty_u16(0)
    levels = {1: 0, 2: 0, 3: 0}
    seq = 0
    t0 = time.ticks_ms()
    next_out, next_diag, next_boot, next_i2c = t0, t0, time.ticks_add(t0, BOOT_MS), t0

    while True:
        now = time.ticks_ms()
        for sid, src in sources.items():
            try:
                if isinstance(src, TFLunaI2C):
                    if time.ticks_diff(now, next_i2c) >= 0:      # poll I2C at 20 Hz (non-blocking UARTs in between)
                        next_i2c = time.ticks_add(now, PERIOD_MS)
                        cm, amp = src.poll()
                        parsers[sid].set_polled(cm, amp, now)
                else:
                    data = src.read_all()
                    if data:
                        parsers[sid].feed(data, now)
            except Exception:
                pass
        for s_ in SENSORS:                # warning levels from the Pico's own sensors
            cm, _amp, st = parsers[s_[0]].reading(now, time.ticks_diff)
            levels[s_[0]] = level_for(cm, st)
        overall = max(levels.values())
        leds["L"].value(led_on(levels[1], now))
        leds["R"].value(led_on(levels[2], now))
        leds["S"].value(led_on(overall, now))         # status LED also covers the rear sensor
        buzzer.duty_u16(BUZZER_VOLUME if buzzer_on(overall, now) else 0)

        if time.ticks_diff(now, next_out) >= 0:
            next_out = time.ticks_add(now, PERIOD_MS)
            seq = (seq + 1) % 1000000
            for s in SENSORS:
                sid, pos = s[0], s[2]
                cm, amp, status = parsers[sid].reading(now, time.ticks_diff)
                print(line("TFL,%d,%d,%d,%s,%d,%d,%s" % (seq, now, sid, pos, cm, amp, status)))
        if time.ticks_diff(now, next_diag) >= 0:
            next_diag = time.ticks_add(now, DIAG_MS)
            for s in SENSORS:
                sid = s[0]
                p = parsers[sid]
                print(line("DIAG,%d,%d,%d,%d,%d,%d" % (seq, now, sid, p.frames_ok, p.frames_bad, p.bytes)))
        if time.ticks_diff(now, next_boot) >= 0:
            next_boot = time.ticks_add(now, BOOT_MS)
            print(boot_line())
        time.sleep_ms(1)


if __name__ == "__main__":
    main()

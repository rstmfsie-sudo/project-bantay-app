# PROJECT B.A.N.T.A.Y. - Raspberry Pi Pico W firmware (MicroPython 1.22+)
# Reads THREE single-beam lidar range finders, sends readings to the PC app over USB,
# and drives the warning LEDs + a quiet buzzer from alert levels the app sends back.
#
# STATUS: written for the Benewake TF-Luna / TFmini(-S / Plus) UART frame. NOT yet run on the real hardware:
# confirm your sensor model, then check the pins below before powering anything.
#
# Wiring (default, SENSOR_TYPE = "benewake_uart"):
#   Left   lidar  TX -> GP1  (UART0 RX)          Right lidar TX -> GP5 (UART1 RX)
#   Centre lidar  TX -> GP9  (PIO UART RX)       Lidar RX pins are not needed (factory default output)
#   Lidar VCC -> 5 V (VBUS, pin 40), GND -> GND. TF-Luna/TFmini TX is 3.3 V logic: safe for the Pico.
#   LEDs: GP16 left, GP17 right, GP18 status (each through a resistor; 12 V lamps need a transistor)
#   Buzzer: GP15 (passive piezo recommended: volume is set by PWM duty below)
#
# Protocol to the app (USB serial, ~20 Hz):   $RF,<ms>,<L_cm>,<R_cm>,<C_cm>,<L_str>,<R_str>,<C_str>*
# From the app:                                !A,<left>,<right>,<status>   0 clear, 1 caution, 2 warning
#
# Copy this file to the Pico as main.py (Thonny: File > Save as > Raspberry Pi Pico). The PC app must
# be closed while Thonny is connected (both use the same USB port).

import sys
import time
import select
import rp2
from machine import Pin, UART, PWM

SENSOR_TYPE = "benewake_uart"     # or "pwm" (pulse-width output, 10 us per cm, e.g. LIDAR-Lite)
PWM_PINS = (2, 3, 4)              # used only when SENSOR_TYPE == "pwm" (as in the BANTAY proposal)
BUZZER_VOLUME = 1500              # PWM duty 0..65535; ~2 % is quiet on a passive piezo. 0 = silent
BUZZER_HZ = 2400
PERIOD_MS = 50                    # 20 Hz output
ALERT_TIMEOUT_MS = 3000           # no message from the app for this long -> lights/buzzer off


# ------------------------------------------------------------------ Benewake UART frame
# 9 bytes: 0x59 0x59 DistL DistH StrL StrH TempL TempH Checksum (low byte of the sum of bytes 0..7)
class BenewakeParser:
    def __init__(self):
        self.buf = bytearray()
        self.cm, self.strength, self.t = -1, -1, 0

    def feed(self, data):
        self.buf.extend(data)
        while len(self.buf) >= 9:
            if self.buf[0] != 0x59 or self.buf[1] != 0x59:
                self.buf = self.buf[1:]
                continue
            f = self.buf[:9]
            if (sum(f[:8]) & 0xFF) == f[8]:
                self.cm = f[2] | (f[3] << 8)
                self.strength = f[4] | (f[5] << 8)
                self.t = time.ticks_ms()
                self.buf = self.buf[9:]
            else:
                self.buf = self.buf[1:]
        if len(self.buf) > 64:
            self.buf = self.buf[-16:]

    def value(self):
        if time.ticks_diff(time.ticks_ms(), self.t) > 500:
            return -1, -1                       # stale: report missing, never an old value
        # strength < 100 or 65535 means the reading is unreliable (Benewake datasheets)
        if self.strength < 100 or self.strength == 65535:
            return -1, self.strength
        return self.cm, self.strength


# PIO UART receiver for the third sensor (MicroPython examples/rp2/pio_uart_rx.py)
@rp2.asm_pio(in_shiftdir=rp2.PIO.SHIFT_RIGHT)
def uart_rx():
    wait(0, pin, 0)
    set(x, 7)                 [10]
    label("bitloop")
    in_(pins, 1)
    jmp(x_dec, "bitloop")     [6]


class PioUart:
    def __init__(self, pin, baud=115200, sm_id=0):
        self.sm = rp2.StateMachine(sm_id, uart_rx, freq=8 * baud, in_base=Pin(pin, Pin.IN, Pin.PULL_UP))
        self.sm.active(1)

    def read(self):
        out = bytearray()
        while self.sm.rx_fifo():
            out.append(self.sm.get() >> 24)
        return out


class PwmSensor:
    def __init__(self, pin):
        from machine import time_pulse_us
        self.pin, self.pulse = Pin(pin, Pin.IN), time_pulse_us

    def value(self):
        us = self.pulse(self.pin, 1, 30000)
        return (us // 10, -1) if us > 0 else (-1, -1)


# ------------------------------------------------------------------ outputs
leds = {"L": Pin(16, Pin.OUT), "R": Pin(17, Pin.OUT), "S": Pin(18, Pin.OUT)}
buzzer = PWM(Pin(15))
buzzer.freq(BUZZER_HZ)
buzzer.duty_u16(0)
alert = {"L": 0, "R": 0, "S": 0}
last_alert_ms = time.ticks_ms()


def drive_outputs(now):
    # LEDs: off clear, 2 Hz caution, 8 Hz warning (same patterns as the BANTAY proposal)
    for k, pin in leds.items():
        lvl = alert[k]
        pin.value(0 if lvl == 0 else (now // (250 if lvl == 1 else 62)) % 2)
    # buzzer: short soft beep each second on caution, two quick beeps every half second on warning
    lvl = alert["S"]
    on = False
    if lvl == 1:
        on = now % 1000 < 60
    elif lvl == 2:
        p = now % 500
        on = p < 50 or 100 <= p < 150
    buzzer.duty_u16(BUZZER_VOLUME if on else 0)


poll = select.poll()
poll.register(sys.stdin, select.POLLIN)
inbuf = ""


def read_app():
    global inbuf, last_alert_ms
    while poll.poll(0):
        ch = sys.stdin.read(1)
        if ch == "\n":
            parts = inbuf.strip().split(",")
            inbuf = ""
            if len(parts) == 4 and parts[0] == "!A":
                try:
                    alert["L"], alert["R"], alert["S"] = (max(0, min(2, int(p))) for p in parts[1:])
                    last_alert_ms = time.ticks_ms()
                except ValueError:
                    pass
        elif len(inbuf) < 40:
            inbuf += ch


# ------------------------------------------------------------------ main
def main():
    if SENSOR_TYPE == "pwm":
        sensors = [PwmSensor(p) for p in PWM_PINS]
        sources = None
    else:
        parsers = [BenewakeParser() for _ in range(3)]
        sources = [UART(0, 115200, rx=Pin(1)), UART(1, 115200, rx=Pin(5)), PioUart(9)]
    next_out = time.ticks_ms()
    while True:
        now = time.ticks_ms()
        if sources:
            for src, parser in zip(sources, parsers):
                data = src.read() if isinstance(src, PioUart) else (src.read() if src.any() else None)
                if data:
                    parser.feed(data)
        read_app()
        if time.ticks_diff(now, last_alert_ms) > ALERT_TIMEOUT_MS:
            alert["L"] = alert["R"] = alert["S"] = 0
        drive_outputs(now)
        if time.ticks_diff(now, next_out) >= 0:
            next_out = time.ticks_add(now, PERIOD_MS)
            vals = [s.value() for s in sensors] if sources is None else [p.value() for p in parsers]
            cm = [v[0] for v in vals]
            st = [v[1] for v in vals]
            print("$RF,%d,%d,%d,%d,%d,%d,%d*" % (now, cm[0], cm[1], cm[2], st[0], st[1], st[2]))
        time.sleep_ms(2)


main()

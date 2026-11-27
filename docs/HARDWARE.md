# Hardware: YDLIDAR T-mini Plus and Raspberry Pi Pico W

## Chosen architecture (direct USB)

```
T-mini Plus ──(its USB adapter board)──► PC USB ──► Python backend (pyserial, 230400 8N1)
Pico W: optional, not in the lidar data path
```

This was chosen with the researcher on 2026-11-26 because it is the simplest reliable path. The Pico W
cannot act as a USB host, and a UART → Pico → USB-CDC bridge adds a failure point at 230400 baud.
The original proposal's firmware (Pico W + Arduino Nano, Wi-Fi UDP telemetry) is **not modified**.

## Protocol: verified sources

| Item | Value | Source |
|---|---|---|
| Commands | `A5 60` scan, `A5 65` stop, `A5 90` info, `A5 92` health, `A5 09/0A/0B/0C` freq ±0.1/±1 Hz, `A5 0D` get freq | Manual §2.2, chart 1 |
| Response header | `A5 5A`, 30-bit length + 2-bit mode, type byte | Manual §2.3 |
| Scan packet | `AA 55` CT LSN FSA LSA CS, then LSN × 3-byte samples | Manual §3.1, chart 4 |
| Sample | byte0 intensity; distance = (S3<<6)+(S2>>2) mm; low 2 bits = flag | Manual §3.1.2–3.1.4 |
| Flags | 2 = specular interference, 3 = ambient light, recommended to filter | Manual §3.1.3 |
| Angle | (FSA>>1)/64 deg, linear interpolation, **clockwise**; no second-level correction for T-mini models | Manual §3.1.5; SDK `isTminiLidar` excludes angle correction |
| Checksum | 16-bit XOR of header words + (0x00,intensity) + distance word | Manual §3.1.6 (worked example `0xE85A` is a unit test) |
| Zero packet CT | (Freq × 10) << 1 \| 1 | Manual chart 5 |
| Motor | spins only after `A5 60`; speed is set by frequency commands (idle mode only) | Manual §4, §5 |
| Distance 0 | invalid (e.g. blocked) | Manual §5 note 3 |
| Model code / baud / range | 151 / 230400 / 0.05–12 m, 6–12 Hz, 8-bit intensity, 4.8–5.2 V | Manual §3.3; SDK `doc/Dataset.md` |

Sources: *YDLIDAR T-mini Plus Development Manual* v1.0 (EAI, 2023-12-21, from ydlidar.com) and
YDLidar-SDK `src/YDlidarDriver.cpp`, `core/common/ydlidar_protocol.h`, `doc/Dataset.md`.

**Not yet confirmed on the physical sensor** (do these at the bench):
1. Plug in the adapter. Hardware should show model 151 and health "all modules normal".
2. Put a box at a tape-measured 1.00 m and 3.00 m. The selected-object distance should agree within about 2–3 cm (manufacturer systematic error is about 20 mm).
3. Put an object at a known bearing (for example to the right of the sensor) and set `angle_offset_deg` and `mirror` so the plot matches reality.
4. Check the `packets rejected` count stays low. A climbing count means cabling or power problems.

## Audit findings on the proposal hardware (not changed, documented for the researcher)

1. **Critical (can damage the Pico):** Arduino Nano TX is 5 V. Wiring it straight to Pico GP9 (3.3 V-only) exceeds the
   pin's rating. Use a divider (for example 1 kΩ / 2 kΩ) or a level shifter.
2. **Critical (data):** `read_scanner()` in Listing 1 does not parse the YDLIDAR protocol. It has no `AA 55` sync, no checksum,
   and fixed byte offsets, so its output is not valid. If the Pico must read the scanner, port `backend/ydlidar.py`'s
   `PacketParser` logic and enlarge the UART RX buffer (`UART(..., rxbuf=4096)`). About 1.1 KB arrives per 50 ms.
3. Listing 1 never sends `A5 60`. Per manual §5 the T-mini Plus will not rotate without it.
4. Three `pulseIn` calls with 30 ms timeouts can take about 90 ms, which breaks the 50 ms sampling period.
5. SoftwareSerial at 115200 on a 16 MHz Nano is unreliable. 57600 is safer, or use the hardware UART.
6. "Metal LED indicators" are commonly 12 V lamps. If so, drive them through a transistor or MOSFET, not straight from a GPIO pin.
7. The range-finder model is not named, so its "10 µs per cm PWM" output is unverified.
8. The Wi-Fi SSID and password are hard-coded, and the UDP target 192.168.4.2 is inconsistent with station mode.

## Power

T-mini Plus: 4.8–5.2 V. Use a USB port or powered hub that can supply the motor start current. If scans stop with
"No complete scan for 5 s", check power first.

## Three TF-Luna sensors on the Pico W (vehicle monitoring)

### Wiring: confirmed on the real Pico (2026-11-27)
The researcher reported Left GP17, Right GP9, Rear GP4/GP5. On those numbers alone, GP5 and GP9 looked like a
UART1 conflict (both are UART1 RX pins). Before uploading anything, the program already on the Pico was read
back (saved in `firmware/pico_backup/main_before_v2_2026-11-27.py`). It shows the real configuration:

| Sensor | Interface | Pins |
|---|---|---|
| 1 · TF-Luna Left | UART mode, **UART0** | GP16 TX → TF-Luna RX, TF-Luna TX → GP17 RX |
| 2 · TF-Luna Right | UART mode, **UART1** | GP8 TX → TF-Luna RX, TF-Luna TX → GP9 RX |
| 3 · TF-Luna Rear | **I2C mode**, **I2C0**, address 0x10 | GP4 SDA, GP5 SCL (TF-Luna pin 5 grounded) |

Every sensor has its own peripheral, so there is **no pin or address conflict and no rewiring is needed**.

**Live test with the old program (2026-11-27), 6 s:**
- Sensor 1: 25/25 readings, 34 to 38 cm, amplitude about 430.
- Sensor 2: 25/25 readings, 3.94 to 3.98 m, amplitude about 980.
- Sensor 3 (I2C): 25/25 readings, 32 to 41 cm.

The firmware also uses LEDs on GP18 / GP19 / GP20 and a buzzer on GP15.

### TF-Luna facts
- **Verified on the Benewake product page:** interfaces UART / I/O / I2C, range 0.2 to 8 m,
  accuracy ±6 cm (0.2 to 3 m), 35 x 21.25 x 13.5 mm.
- **From the Benewake user manual, not re-checked here because the PDF is behind a download form:**
  - 115200 8N1;
  - 9-byte frame `59 59 DistL DistH AmpL AmpH TempL TempH Checksum` (low byte of the sum of bytes 0..7), distance in cm;
  - Amp < 100 or 0xFFFF means unreliable;
  - pin 5 tied to GND selects I2C.
- The firmware is defensive about these unverified details. It validates every checksum, and it reports
  `BADFRAMES` if bytes arrive but no frame validates (wrong baud rate or format).

### Pico to app protocol (firmware v2)
- **Data lines:** `$TFL,<seq>,<ms>,<id>,<pos>,<dist_cm>,<amp>,<status>*HH`, about 20 Hz per sensor.
  - Status is OK, WEAK, STALE, NODATA or BADFRAMES.
  - The id must match its position (1 = L, 2 = R, 3 = B). The app rejects anything else.
- **Other lines:** `$DIAG,...` once per second, and `$BOOT,...` at start and every 10 s.
- **Checksum:** NMEA-style XOR of the characters between `$` and `*`.
- **App to Pico:** `!A,<left>,<right>,<overall>`, levels 0 (safe) to 3 (danger), for the LEDs and buzzer.

### Still to do on the real hardware
1. Firmware v2 was uploaded to the Pico on 2026-11-27. The USB devices were then unplugged before its output
   could be read. Plug back in: the Hardware page should show all three sensors `OK`.
2. Check that every sensor reports `OK` with a sensible distance. Hold a card at a tape-measured distance.
3. Measure the real mounting positions and directions. Enter them in Settings, under Vehicle geometry,
   then tick "I measured these".

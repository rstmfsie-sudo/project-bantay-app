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

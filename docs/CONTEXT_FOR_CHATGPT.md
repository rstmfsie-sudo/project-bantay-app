# BANTAY LiDAR: project context (handoff)

This file summarizes a long working session in which BANTAY LiDAR was built.
It is written so another assistant can continue the work without the original chat.

---

## 1. What the project is

- **Project B.A.N.T.A.Y.** ("Blind Spot Alert and Near-Field Tracking Aid for Yield-Warning") is a Philippine
  high-school science-fair (RSTMF) research project. The original proposal describes a **vehicle-mounted blind-spot
  warner** for motorcycles and tricycles (Pico W + Arduino Nano + range finders + MPU-6050 + LEDs, Wi-Fi telemetry).
- The researcher then asked for a **local, offline desktop application** called **BANTAY LiDAR**. It does
  360° LiDAR monitoring, object detection, tracking and classification, with a premium macOS-inspired look.
- **Decision made by the user:** the app is a **stationary roadside 360° monitor**, not the vehicle blind-spot device.
- **No app code existed before.** The only prior code was two firmware listings printed inside the proposal (.docx).
  The proposal's firmware was audited but **not modified**.

## 2. Hardware (actual, verified on the user's unit)

| Item | Status |
|---|---|
| **YDLIDAR T-mini Plus** (2D single-plane, 360°) | **The only sensor in use.** Model code 151, firmware 1.1, healthy, ~6 Hz, 0 bad packets |
| USB adapter | Silicon Labs **CP2102** on **COM6**. Needed the CP210x driver; it showed Windows error code 28 until installed |
| Raspberry Pi Pico W | Owned but **not used** (direct-USB architecture chosen). The app only detects it by USB VID 0x2E8A |
| 3 range finders, MPU-6050, Arduino Nano | From the proposal. **Not connected to the app.** Range-finder model unknown |
| Mounting rotation | `angle_offset_deg = 90` so the sensor's front points **up** on screen (user reported a 90° misalignment) |

**Architecture chosen by the user:** T-mini Plus → its USB adapter → PC → Python backend (direct USB, no Pico in the data path).

### Protocol (verified against the official T-mini Plus Development Manual v1.0 and the YDLidar-SDK)
- Commands are `A5 xx`: `A5 60` start scan (the motor only spins after this), `A5 65` stop, `A5 90` device info,
  `A5 92` health, `A5 09/0A/0B/0C` frequency ±0.1/±1 Hz (idle only), `A5 0D` get frequency.
- Responses start with `A5 5A`, then a 30-bit length + 2-bit mode, then a type byte.
- Scan packets start with `AA 55`, then CT, LSN, FSA, LSA, CS, followed by LSN × 3-byte samples:
  - **intensity** is byte 0;
  - **distance (mm)** = `(S3<<6) + (S2>>2)`, which is the 16-bit little-endian value `>> 2`;
  - the low 2 bits are a **flag**: 2 = specular interference, 3 = ambient light.
- Angle = `(raw>>1)/64` degrees, **clockwise**, linearly interpolated, with no angle correction for T-mini models.
- Checksum: 16-bit XOR (the manual's example packet, checksum `0xE85A`, is a unit test).
- The zero packet's CT byte = `(Freq*10)<<1 | 1`. A distance of 0 means invalid.

### Audit findings on the proposal's hardware and firmware (documented, not changed)
1. Arduino Nano TX (5 V) wired straight to a Pico GPIO (3.3 V) can **damage the Pico**. It needs a divider or level shifter.
2. The proposal's `read_scanner()` does **not** parse the YDLIDAR protocol (no header sync or checksum, fixed offsets).
3. The proposal never sends `A5 60`, so the T-mini Plus would never spin.
4. Three `pulseIn` calls with 30 ms timeouts break the 50 ms loop. SoftwareSerial at 115200 is unreliable.
5. "Metal LED indicators" may be 12 V lamps, which need a transistor. The Wi-Fi settings are hard-coded and inconsistent.

## 3. What was built

**Location:** `C:\Users\Admin\Downloads\VS CODE\`

### Stack
- **Backend:** Python 3.12, FastAPI, Uvicorn, pyserial, NumPy, SciPy, scikit-learn, SQLite, and optional PyTorch (CPU).
- **Frontend:** React + TypeScript (Vite), Canvas 2D, plain CSS. No Tailwind, Three.js or chart library.
- **Desktop app:** Electron (`desktop/`) around the Python server (`bantay_server.py`, PyInstaller), shipped as an NSIS installer in `app\installer\`.
  A **desktop and Start-menu icon** opens it in its own window, with no browser and no console.
- Fully **offline**: it binds to 127.0.0.1:8765, and the fonts are bundled.

### Pipeline
```
serial thread (parse + verify packets, assemble revolutions, auto-reconnect)
  → bounded queue (drops oldest, never blocks acquisition)
  → processing: filter (range, flags) → polar→Cartesian → adaptive background → DBSCAN
      → object features → classification → Kalman tracking → SQLite log
  → WebSocket → React canvas
```

### Key features
- **Live Scan:** polar grid, zoom and pan, layers (rejected raw / filtered / clusters / tracks / labels / trails),
  click to select an object, and a "Perspective" toggle clearly labelled **decorative** (the data is 2D).
- **Adaptive background**, which updates itself because the road keeps changing:
  - a snapshot every 2 s, with a per-angle-bin median over a window of 2 × `background_absorb_s` (default 30 s);
  - bins with no return count as +∞, so passing traffic never becomes background;
  - an object that stays still for about 30 s (a parked car) is absorbed;
  - there is no "learn empty road" step, and a "Re-learn now" button handles the case where the sensor is moved.
- **Tracking:** a constant-velocity Kalman filter with gated Hungarian assignment. Speed and heading appear only
  after enough frames and are labelled "est.". TTC and risk use the proposal's thresholds
  (WARNING inside 2 m or TTC under 1.5 s, CAUTION inside 4 m or TTC under 3 s).
- **Classification methods are kept separate:** detection only / rule-based / ML experimental / ML validated.
  - Rules give **no confidence score**.
  - Motorcycle and bicycle are **not separated by the rules**; both become "unknown, two-wheeler candidate".
  - An ML result below 0.6 confidence becomes Unknown.
- **ML uses object-only features**, so a model trained at one road transfers to another:
  length, width, linearity, number of segments (gaps: legs, wheels), **coverage** (share of laser beams across the
  object that returned; independent of distance), mean intensity, largest length over the track, and speed.
  Distance, point count, angular span and time-in-view were **deliberately removed**: they describe the site.
- **Two models:**
  - **Random Forest:** needs 2+ categories with 3+ distinct objects each.
  - **PointNet-style neural network** (PyTorch): learns from the raw points (centred and rotated) plus the features.
    It needs 10+ objects per category, and realistically hundreds before it beats the forest.
  - Both are evaluated with **StratifiedGroupKFold grouped by tracked object**, so the score is never inflated by
    testing on frames of an object seen in training. Accuracy, a confusion matrix and per-class precision, recall and
    F1 are reported. "Validated" is set only by the researcher.
- **Dataset tools:**
  - **Label & follow:** one click keeps capturing a sample every 0.5 s while the object is visible.
  - **Labeling from recordings:** replay with pause, step, seek and 0.25×–2× speed, showing the recorded clock so it can
    be matched against a phone video. Replays never write to Data History.
  - Relabel or delete samples, and export the dataset as CSV or JSON.
- **Recording:** raw revolutions are saved as JSONL, preserving the original measurements.
- **Pages:** Overview, Live Scan, Object Detection, Tracking, Analytics, Data History (filter, CSV export),
  Dataset Manager, Hardware, Machine Learning, Settings.
- **Startup screen** shows only real checks; it never fakes a sensor connection.
- **Reliability:**
  - Single instance: a second launch just reopens the window.
  - If the lidar was working and stays unplugged for 20 s (configurable), a countdown banner appears and the app
    closes cleanly.
  - Closing the window stops the lidar motor.

### Design
- **Palette:** Charcoal #252321, Terracotta #C96F4A, Deep Clay #A34F36, Cream #F4EBDD, Warm Orange #E58A52, with
  dark and light themes.
- **Fonts** (matched to a reference "cheat sheet" image the user supplied): **Poppins** for titles and text,
  **Lora Italic** for the orange subtitle lines, **JetBrains Mono** for the `[ BRACKET ]` tags and labels.
- **Playful elements:** bracket tag pills, "NEXT: … →" links, window dots on card titles, tilted stamps
  ("2D · SINGLE PLANE", "● LIVE", "SAVE THIS ONE"), and a numbered six-step research workflow with progress dots.
- **Logo:** an orange arch with a dot (the "Vigil" concept), also used as the app icon.

## 4. Honesty rules the user required (built into the software)
- **No simulated or fake sensor data in the app.** With no sensor, it shows an empty grid and says so.
  Test fixtures live only in tests or in scratch folders via the `BANTAY_DATA` override.
- **No fabricated ML accuracy or confidence.** Metrics exist only after training on the user's own labeled samples.
- **2D data is never presented as 3D.**
- **No claims of physical testing beyond what was actually done.** Done so far: the lidar connected, model/health
  read, scanning stable, background behaviour checked in a room. **Not yet done:** roadside traffic testing,
  tape-measure distance check, any trained model (the dataset is still empty).

## 5. Key files
| Path | Role |
|---|---|
| `backend/ydlidar.py` | protocol parser, revolution assembler, serial driver thread |
| `backend/processing.py` | filtering, polar→Cartesian, adaptive `Background`, DBSCAN, `cluster_features` |
| `backend/classify.py` | rules, object-only `ML_FEATURES`, Random Forest with grouped CV |
| `backend/pointnet.py` | optional PointNet-style network (PyTorch) |
| `backend/tracking.py` | Kalman tracker, TTC / risk |
| `backend/engine.py` | pipeline glue, recording, replay with transport controls, follow-labeling, unplug auto-exit |
| `backend/app.py` | FastAPI REST + WebSocket `/ws` |
| `backend/db.py` | SQLite: detections, samples (with session for object grouping) |
| `backend/config.py` | all tunables, saved to `config.json` |
| `frontend/src/` | `App.tsx` (shell, boot), `ScanCanvas.tsx`, `views/`, `Workflow.tsx`, `ReplayBar.tsx`, `styles.css` |
| `bantay_server.py` / `desktop/` | headless server started by the Electron desktop shell |
| `build_app.ps1` | builds the frontend, server and Windows installer |
| `run.py` / `start.bat` | browser / developer mode |
| `tests/` | 21 pytest tests (protocol, pipeline, background, ML, replay, follow, unplug) |
| `docs/HARDWARE.md` | protocol sources, bench checklist, audit findings |
| `CLAUDE.md` | project rules |

- **App data** (db, recordings, models, config, log): `%LOCALAPPDATA%\BANTAY LiDAR`.
- **Source-mode data:** `data\`.

## 6. Standing rule: every update must reach the app
After any change, run the tests, close the app (it holds COM6 and port 8765), run
`powershell -ExecutionPolicy Bypass -File build_app.ps1` (about 5 minutes), relaunch it from the desktop icon,
and confirm the lidar is scanning. Only then is the change done.

## 7. Current state and next steps
- The app is installed and working with the real sensor. **The dataset is empty, so no model is trained yet.**
- **Researcher's next steps:**
  1. Record a roadside session together with a phone video.
  2. Label objects from the recording (with Follow).
  3. Train the Random Forest. Try the neural network once there are 10+ objects per category.
  4. Validate on another day or site.
- **Bench checks still to do:**
  - tape-measure distances at 1 m and 3 m (expect about ±2–3 cm);
  - walk around the sensor clockwise and confirm the dot also moves clockwise (if not, flip `mirror`).
- **Possible future work:**
  - integrate the MPU-6050 or range finders (needs their model names and wiring; fix the 5 V→3.3 V issue first);
  - relabel bearings so "straight ahead" reads 0°;
  - roadside field validation.
- **Known limitations:**
  - a single horizontal slice cannot reliably separate motorcycles from bicycles;
  - occlusion; centroid shift biases speed;
  - a vehicle stopped longer than the absorb time fades into the background (configurable);
  - the rule thresholds are heuristics to calibrate on site.

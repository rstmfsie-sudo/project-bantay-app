# PROJECT B.A.N.T.A.Y.: project rules

## Every change must reach the desktop app
The user runs the INSTALLED Electron app from the desktop/Start-menu icon "PROJECT B.A.N.T.A.Y."
(`%LOCALAPPDATA%\Programs\project-bantay\PROJECT BANTAY.exe`). Electron (`desktop/`) starts the bundled Python server
(`bantay_server.py`, PyInstaller). The install is a snapshot: code changes do nothing until rebuilt and reinstalled.
After ANY change to `backend/`, `frontend/src/`, `bantay_server.py`, `desktop/` or dependencies:

1. `.venv\Scripts\python -m pytest tests`: all must pass.
2. Close the running app (it holds COM6 and port 8765; also stop any `bantay-server.exe`).
3. `powershell -ExecutionPolicy Bypass -File build_app.ps1` (frontend + server + installer, 10+ minutes).
4. Install it: `& "app\installer\PROJECT-BANTAY-Setup-<version>.exe" /S`.
5. Launch from the desktop icon and check `http://127.0.0.1:8765/api/status` shows the lidar scanning.
For a public release, bump `desktop/package.json` version and attach the installer to a GitHub Release
(it is too big for git).

Only then report the change as done.

## Data
- The packaged app's data (db, recordings, models, config, log) is in `%LOCALAPPDATA%\BANTAY LiDAR`. Rebuilds never touch it.
- `data\` in this folder is only used when running from source (`start.bat` / `run.py`).
- Never put synthetic/test scans into either data folder. Use `BANTAY_DATA=<scratch dir>` for experiments.

## Hardware facts (verified on the user's unit)
- YDLIDAR T-mini Plus, model code 151, firmware 1.1, CP2102 adapter on COM6, ~6 Hz.
- Mounting rotation: `angle_offset_deg = 90` makes the sensor's front point up on screen (user-confirmed fix).

- A Raspberry Pi Pico W (COM5, MicroPython 1.29) holds firmware v2 from `firmware/pico_rangefinders/main.py` (uploaded 2026-11-27).
  The three range finders are **TF-Luna**. The real wiring was confirmed from the program on the Pico:
  - Left: UART0, GP16/17.
  - Right: UART1, GP8/9.
  - Rear: I2C0, GP4/5, address 0x10.
  All three answered in a live test. Firmware v2 (compiles with mpy-cross) was uploaded on 2026-11-27; its live
  output has not been observed yet. The original program is in firmware/pico_backup/. Do not change the wiring.
- Vehicle frame: x forward, y left, origin at the vehicle centre. Mounting values are configurable and
  UNCALIBRATED until measured.

## Honesty rules for the digital twin
- Twin-trained models use method `ml_synthetic` ("ML (digital twin)"). Their accuracy is on simulated objects only.
- Simulated scans never go into the live view, the real dataset or Data History.

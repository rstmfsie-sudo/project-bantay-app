# PROJECT B.A.N.T.A.Y.: project rules

## Every change must reach the desktop app
The user runs the app from the desktop/Start-menu icon "PROJECT B.A.N.T.A.Y.", which opens the packaged
`app\PROJECT BANTAY\PROJECT BANTAY.exe`. That exe is a snapshot: code changes do nothing for the user until it is rebuilt.
After ANY change to `backend/`, `frontend/src/`, `bantay_app.py` or dependencies:

1. `.venv\Scripts\python -m pytest tests`: all must pass.
2. Close the running app (it holds COM6 and port 8765).
3. `powershell -ExecutionPolicy Bypass -File build_app.ps1` (rebuilds the frontend + exe, several minutes).
4. Launch the exe and check `http://127.0.0.1:8765/api/status` shows the lidar scanning.

Only then report the change as done.

## Data
- The packaged app's data (db, recordings, models, config, log) is in `%LOCALAPPDATA%\BANTAY LiDAR`. Rebuilds never touch it.
- `data\` in this folder is only used when running from source (`start.bat` / `run.py`).
- Never put synthetic/test scans into either data folder. Use `BANTAY_DATA=<scratch dir>` for experiments.

## Hardware facts (verified on the user's unit)
- YDLIDAR T-mini Plus, model code 151, firmware 1.1, CP2102 adapter on COM6, ~6 Hz.
- Mounting rotation: `angle_offset_deg = 90` makes the sensor's front point up on screen (user-confirmed fix).

- A Raspberry Pi Pico W is plugged in over USB, but it is not yet running `firmware/pico_rangefinders/main.py`.
  The three range finders are **TF-Luna** (user-confirmed 2026-11-26). The firmware uses TF-Luna UART frames and has not been run on hardware yet.

## Honesty rules for the digital twin
- Twin-trained models use method `ml_synthetic` ("ML (digital twin)"). Their accuracy is on simulated objects only.
- Simulated scans never go into the live view, the real dataset or Data History.

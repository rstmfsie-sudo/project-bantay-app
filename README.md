# PROJECT B.A.N.T.A.Y.

A local, offline 360° roadside monitoring and object classification workstation for the
**YDLIDAR T-mini Plus** (2D single-plane lidar), built for the Project B.A.N.T.A.Y. research prototype.

## Desktop app

Double-click **PROJECT B.A.N.T.A.Y.** on the desktop or in the Start menu. It opens in its own frameless window,
with no browser or console. Closing the window stops the lidar motor and exits.

- App: `app\PROJECT BANTAY\PROJECT BANTAY.exe` (keep the whole folder together)
- Your data (database, recordings, models, settings, log): `%LOCALAPPDATA%\BANTAY LiDAR`
- Rebuild after code changes: `powershell -ExecutionPolicy Bypass -File build_app.ps1`

## Run from source (developer / browser mode)

```powershell
start.bat                      # or: .venv\Scripts\python run.py
```
The app opens at http://127.0.0.1:8765. It binds to localhost only and needs no internet.
Options: `--port 9000`, `--no-browser`.

First-time setup (needs internet once):
```powershell
py -3.12 -m venv .venv                     # or the full path to python 3.12
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cpu   # optional: neural network
cd frontend; npm install; npm run build
```
Tests: `.venv\Scripts\python -m pytest tests`

## Using it at the roadside

1. Plug in the T-mini Plus USB adapter. **Hardware** shows the port, model, health and frequency.
2. With the road clear, press **Learn background** in Hardware. Static walls and poles are then ignored.
3. Turn on **Record raw scans** to keep every original revolution for later analysis or replay.
4. In **Live Scan**, click an object you can identify and label it. This builds the dataset.
5. **Label from recordings** (easier than labeling live): in Live Scan, set *Source → Recording*, pause,
   click an object and label it. The transport bar shows the recorded clock, which you can match to a phone video filmed at the same time.
   Replays never add rows to Data History.
6. When each category has enough objects, open **Machine Learning** and train:
   - **Random Forest**: 12 measurements per object; needs 3+ objects per category. Start here.
   - **Neural network** (PointNet-style, PyTorch): learns from the raw points too; needs 10+ objects per category,
     and realistically hundreds before it beats the forest.
   Both are scored with cross-validation **grouped by object**: the test set never contains frames of an object seen in training.

## Three range finders (Raspberry Pi Pico W)

Flash `firmware/pico_rangefinders/main.py` to the Pico as `main.py` with Thonny (close the app first).
It reads three TF-Luna/TFmini-style lidars (left TX on GP1, right TX on GP5, centre TX on GP9, 5 V power),
sends readings at 20 Hz, and drives the warning LEDs (GP16/17/18) and a quiet buzzer (GP15).
The app finds the Pico automatically and draws the three beams on the live scan.
**Not yet tested on the real sensors.** Confirm the model first; set `SENSOR_TYPE = "pwm"` for pulse-width lidars.

## Digital twin

**Digital Twin** simulates the T-mini Plus on a pretend road with 3D models of cars, motorcycles, bicycles
and pedestrians, sliced at your mounting height. Training there gives a starter model, labelled
"ML (digital twin)". Its score is on simulated objects only. Once real labels exist it is also scored on them.

## Architecture

```
serial thread (ydlidar.py) ─► bounded queue ─► processing thread ─► latest frame ─► WebSocket ─► React canvas
  parse/verify packets          (drops oldest,     filter → background → DBSCAN       (25 Hz max)
  assemble revolutions           never blocks)     → features → classify → Kalman
                                                    tracking → SQLite log
```

| Path | Role |
|---|---|
| `backend/ydlidar.py` | T-mini Plus protocol (verified against the manual), driver thread, auto-reconnect |
| `backend/processing.py` | validation, flag filtering, polar→Cartesian, background model, DBSCAN, features |
| `backend/classify.py` | rule-based classifier, Random Forest training and inference |
| `backend/pointnet.py` | optional PointNet-style neural classifier (PyTorch, CPU) |
| `backend/tracking.py` | Kalman tracking, TTC and risk levels (proposal thresholds) |
| `backend/db.py` | SQLite: detections and labeled samples |
| `backend/engine.py`, `app.py` | pipeline glue, recording/replay, REST and WebSocket API |
| `frontend/` | React + TypeScript UI (Canvas 2D, no CSS or chart frameworks) |
| `data/` | config, database, recordings, models, logs (created at runtime) |

## Honesty rules built into the software

- **No simulated data in the app.** With no sensor connected the UI shows an empty grid and says so.
  Replay plays only real recordings and is labelled REPLAY.
- **Separate classification methods.** Every detection records `detection`, `rule_based`,
  `ml_experimental` or `ml_validated`, plus its model version.
- **No confidence from the rules.** ML confidence is the model's uncalibrated class probability. Below the threshold the result is *unknown*.
- **Measured accuracy only.** It is reported only after training, as stratified k-fold cross-validation on your own samples.
  "Validated" is set only by the researcher, after independent field testing.
- **Speed shown as an estimate.** It appears only after enough consecutive scans and is labelled "est.".
- **2D shown as 2D.** The Perspective toggle is decorative and labelled as such.

## Known limitations

- A single horizontal plane sees one cross-section. **Motorcycle and bicycle are not separable by the rules**
  (both are reported *unknown*, "two-wheeler candidate"), and an ML model will likely confuse them too.
- Objects hidden behind others are not seen, and a cluster's centroid shifts as its visible side changes. This biases speed and heading.
- The rule thresholds in `backend/classify.py` are heuristics and must be calibrated on site.
- Physical hardware testing has **not** been done. See the bench checklist in `docs/HARDWARE.md`.
- Pico W is detected (by USB VID) but not used. No Pico firmware was written or changed.

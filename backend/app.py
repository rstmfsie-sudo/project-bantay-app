"""FastAPI app: REST for control/history, one WebSocket for live frames. Serves the built frontend."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .classify import CATEGORIES, ML_FEATURES
from .config import DATA, ROOT
from .engine import Engine

engine = Engine()


@asynccontextmanager
async def lifespan(_app):
    engine.start()
    yield


app = FastAPI(title="BANTAY LiDAR", docs_url="/api/docs", lifespan=lifespan)


def csv_response(rows: list[dict], cols: list[str], name: str):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader(); w.writerows(rows)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------- status / config
@app.get("/api/status")
def status():
    return engine.status()


@app.get("/api/config")
def get_config():
    return engine.cfg.get()


@app.patch("/api/config")
def patch_config(patch: dict):
    if "source" in patch and patch["source"] not in ("serial", "replay", "simulation"):
        raise HTTPException(400, "source must be serial, replay or simulation")
    if "classifier" in patch and patch["classifier"] not in ("rules", "ml", "nn"):
        raise HTTPException(400, "classifier must be rules, ml or nn")
    try:
        cfg = engine.cfg.update(patch)
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(400, f"Invalid setting: {e}")
    if {"source", "port", "baud"} & patch.keys():
        engine.driver.restart()
    if {"source", "replay_file"} & patch.keys():
        engine.replay.play()
    return cfg


@app.post("/api/lidar/reconnect")
def reconnect():
    engine.driver.restart()
    return {"ok": True}


class Freq(BaseModel):
    hz: float


@app.post("/api/lidar/frequency")
def set_frequency(f: Freq):
    if not 6.0 <= f.hz <= 12.0:   # manufacturer range
        raise HTTPException(400, "T-mini Plus scan frequency range is 6-12 Hz")
    engine.driver.request_frequency(f.hz)
    return {"ok": True, "note": "Scanning restarts; the sensor only accepts frequency commands while idle."}


@app.post("/api/background/reset")
def reset_background():
    """Forget the scene and rebuild it (e.g. after moving the sensor). Takes ~10 s."""
    engine.bg.reset()
    return {"ok": True}


@app.get("/api/recordings")
def recordings():
    files = sorted((DATA / "recordings").glob("*.jsonl"), reverse=True)
    return [{"name": p.name, "bytes": p.stat().st_size, "modified": p.stat().st_mtime} for p in files]


@app.post("/api/replay/play")
def replay_play():
    engine.replay.play()
    return {"ok": True}


class ReplayCtl(BaseModel):
    action: str            # play | pause | step | seek | speed
    value: float = 1


@app.post("/api/replay/control")
def replay_control(c: ReplayCtl):
    r = engine.replay
    try:
        {"play": lambda: r.play(), "pause": lambda: r.pause(), "step": lambda: r.step(c.value),
         "seek": lambda: r.seek(c.value), "speed": lambda: r.set_speed(c.value)}[c.action]()
    except KeyError:
        raise HTTPException(400, "action must be play, pause, step, seek or speed")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return r.state()


# ---------------------------------------------------------------- history / analytics
@app.get("/api/detections")
def detections(since: float | None = None, until: float | None = None, category: str | None = None,
               method: str | None = None, track_id: int | None = None, limit: int = 200, offset: int = 0):
    total, rows = engine.db.detections(since, until, category, method, track_id, min(limit, 5000), offset)
    return {"total": total, "rows": rows}


@app.get("/api/detections.csv")
def detections_csv(since: float | None = None, until: float | None = None, category: str | None = None,
                   method: str | None = None):
    from .db import DET_COLS
    _, rows = engine.db.detections(since, until, category, method, None, 10**7, 0)
    return csv_response(rows, DET_COLS, "bantay-detections.csv")


@app.get("/api/stats")
def stats(hours: float = 24):
    bucket = 60 if hours <= 2 else 600 if hours <= 48 else 3600
    return engine.db.stats(time.time() - hours * 3600, bucket) | {"bucket_s": bucket}


# ---------------------------------------------------------------- dataset
class Label(BaseModel):
    track_id: int
    label: str
    notes: str = ""
    follow: bool = False   # keep capturing this object every 0.5 s until it leaves


@app.post("/api/dataset")
def add_sample(l: Label):
    if l.label not in CATEGORIES:
        raise HTTPException(400, f"Label must be one of {CATEGORIES}")
    try:
        return {"id": engine.label_track(l.track_id, l.label, l.notes, l.follow)}
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/dataset/follow/{track_id}")
def stop_follow(track_id: int):
    engine.stop_follow(track_id)
    return {"ok": True}


@app.get("/api/dataset")
def list_samples(label: str | None = None, points: bool = False):
    rows = engine.db.samples(label)
    if not points:
        for r in rows:
            r["n_points"] = len(r.pop("points"))
    return rows


@app.patch("/api/dataset/{sid}")
def relabel(sid: int, body: dict):
    if body.get("label") not in CATEGORIES:
        raise HTTPException(400, "bad label")
    engine.db.relabel_sample(sid, body["label"])
    return {"ok": True}


@app.delete("/api/dataset/{sid}")
def delete_sample(sid: int):
    engine.db.delete_sample(sid)
    return {"ok": True}


@app.get("/api/dataset/export.csv")
def export_dataset_csv():
    rows = [{"id": s["id"], "ts": s["ts"], "label": s["label"], "track_id": s["track_id"], **s["features"]}
            for s in engine.db.samples()]
    return csv_response(rows, ["id", "ts", "label", "track_id"] + ML_FEATURES, "bantay-dataset.csv")


@app.get("/api/dataset/export.json")
def export_dataset_json():
    body = json.dumps({"features": ML_FEATURES, "samples": engine.db.samples()})
    return StreamingResponse(iter([body]), media_type="application/json",
                             headers={"Content-Disposition": 'attachment; filename="bantay-dataset.json"'})


# ---------------------------------------------------------------- ML
class TrainJob(BaseModel):
    data: str = "twin"                 # real | twin | both
    models: list[str] = ["rf", "nn"]
    height_m: float = 0.6              # LiDAR scan-plane height used by the digital twin
    objects_per_class: int = 60
    activate: bool = True              # switch the app to the better model when done


@app.post("/api/ml/train")
def train(b: TrainJob):
    if b.data not in ("real", "twin", "both") or not set(b.models) <= {"rf", "nn"} or not b.models:
        raise HTTPException(400, "data must be real, twin or both; models from rf, nn")
    if not 0.1 <= b.height_m <= 1.8 or not 10 <= b.objects_per_class <= 300:
        raise HTTPException(400, "height 0.1-1.8 m, 10-300 objects per class")
    try:
        return engine.start_ml_job(b.data, b.models, b.height_m, b.objects_per_class, b.activate)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/api/dataset/synthetic.csv")
def export_synthetic_csv():
    from .twin import load_dataset
    ds = load_dataset()
    if not ds:
        raise HTTPException(404, "No synthetic dataset yet: generate it on the Digital Twin page")
    rows = [{"object": g, "label": lab, **dict(zip(ML_FEATURES, x))} for x, lab, g in zip(ds["X"], ds["y"], ds["groups"])]
    return csv_response(rows, ["object", "label"] + ML_FEATURES, "bantay-synthetic-dataset.csv")


@app.get("/api/twin/preview")
def twin_preview(height_m: float = 0.6):
    from .twin import preview
    return preview(max(0.05, min(height_m, 1.8)))


@app.post("/api/ml/validated")
def set_validated(body: dict):
    try:
        m = engine.nn if body.get("model") == "nn" else engine.ml
        m.set_validated(bool(body.get("value")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return m.meta


# ---------------------------------------------------------------- live
@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    seq, last_status, alert_seq, last_rf = -1, 0.0, -1, 0.0
    try:
        while True:
            await asyncio.sleep(0.04)
            if engine.seq != seq and engine.frame is not None:
                seq = engine.seq
                await sock.send_text(json.dumps({"type": "frame", "frame": engine.frame}))
            if engine.alert_seq != alert_seq:
                alert_seq = engine.alert_seq
                await sock.send_text(json.dumps({"type": "alert", "alert": engine.alert}))
            if time.time() - last_rf > 0.1:
                last_rf = time.time()
                await sock.send_text(json.dumps({"type": "rf", "rf": engine.rf_frame()}))
            if time.time() - last_status > 1:
                last_status = time.time()
                st = await asyncio.to_thread(engine.status)   # port enumeration can block
                await sock.send_text(json.dumps({"type": "status", "status": st}, default=str))
    except (WebSocketDisconnect, RuntimeError):
        pass


DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() and DIST in f.resolve().parents else DIST / "index.html")

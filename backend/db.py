"""SQLite storage: detection history and the labeled research dataset."""
import json
import sqlite3
import threading
import time

from .config import DATA

DB_FILE = DATA / "bantay.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS detections (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, track_id INTEGER,
  category TEXT, method TEXT, confidence REAL, model_version TEXT,
  distance REAL, angle REAL, x REAL, y REAL, speed REAL, length REAL, n_points INTEGER, risk TEXT);
CREATE INDEX IF NOT EXISTS ix_det_ts ON detections(ts);
CREATE TABLE IF NOT EXISTS samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, label TEXT NOT NULL, track_id INTEGER,
  features TEXT NOT NULL, points TEXT NOT NULL, notes TEXT, source TEXT);
"""
DET_COLS = ["id", "ts", "track_id", "category", "method", "confidence", "model_version",
            "distance", "angle", "x", "y", "speed", "length", "n_points", "risk"]


class DB:
    def __init__(self, path=DB_FILE):
        self.lock = threading.Lock()
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(SCHEMA)
        try:   # added later: groups samples of one physical object (track ids restart each run)
            self.con.execute("ALTER TABLE samples ADD COLUMN session REAL")
        except sqlite3.OperationalError:
            pass

    def _q(self, sql, args=()):
        with self.lock:
            cur = self.con.execute(sql, args)
            self.con.commit()
            return cur

    def add_detection(self, d: dict):
        self._q(f"INSERT INTO detections ({','.join(DET_COLS[1:])}) VALUES ({','.join('?' * 14)})",
                [d.get(k) for k in DET_COLS[1:]])

    def detections(self, since=None, until=None, category=None, method=None, track_id=None,
                   limit=500, offset=0):
        where, args = [], []
        for col, op, v in (("ts", ">=", since), ("ts", "<=", until), ("category", "=", category),
                           ("method", "=", method), ("track_id", "=", track_id)):
            if v not in (None, ""):
                where.append(f"{col} {op} ?"); args.append(v)
        w = f"WHERE {' AND '.join(where)}" if where else ""
        total = self._q(f"SELECT COUNT(*) FROM detections {w}", args).fetchone()[0]
        rows = self._q(f"SELECT {','.join(DET_COLS)} FROM detections {w} ORDER BY ts DESC LIMIT ? OFFSET ?",
                       args + [limit, offset]).fetchall()
        return total, [dict(zip(DET_COLS, r)) for r in rows]

    def stats(self, since: float, bucket_s: int):
        by_cat = self._q("SELECT category, method, COUNT(DISTINCT track_id) FROM detections WHERE ts >= ? "
                         "GROUP BY category, method", (since,)).fetchall()
        series = self._q("SELECT CAST(ts / ? AS INTEGER) * ?, category, COUNT(DISTINCT track_id) "
                         "FROM detections WHERE ts >= ? GROUP BY 1, 2 ORDER BY 1",
                         (bucket_s, bucket_s, since)).fetchall()
        dist = self._q("SELECT CAST(distance AS INTEGER), COUNT(*) FROM detections WHERE ts >= ? "
                       "GROUP BY 1 ORDER BY 1", (since,)).fetchall()
        return {"by_category": [{"category": c, "method": m, "objects": n} for c, m, n in by_cat],
                "series": [{"t": t, "category": c, "objects": n} for t, c, n in series],
                "distance_hist": [{"m": m, "count": n} for m, n in dist]}

    def purge(self, retention_days: int):
        self._q("DELETE FROM detections WHERE ts < ?", (time.time() - retention_days * 86400,))

    # ---------------------------------------------------------------- dataset
    def add_sample(self, label, track_id, features, points, notes="", source="live", session=None):
        cur = self._q("INSERT INTO samples (ts, label, track_id, features, points, notes, source, session) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (time.time(), label, track_id, json.dumps(features), json.dumps(points), notes, source, session))
        return cur.lastrowid

    def samples(self, label=None):
        sql = "SELECT id, ts, label, track_id, features, points, notes, source, session FROM samples"
        rows = self._q(sql + (" WHERE label = ?" if label else "") + " ORDER BY ts DESC",
                       (label,) if label else ()).fetchall()
        return [{"id": r[0], "ts": r[1], "label": r[2], "track_id": r[3], "features": json.loads(r[4]),
                 "points": json.loads(r[5]), "notes": r[6], "source": r[7],
                 "object": f"{r[8]}:{r[3]}"} for r in rows]

    def relabel_sample(self, sid, label):
        self._q("UPDATE samples SET label = ? WHERE id = ?", (label, sid))

    def relabel_object(self, session, track_id, label):
        self._q("UPDATE samples SET label = ? WHERE session = ? AND track_id = ?", (label, session, track_id))

    def dataset_summary(self):
        rows = self._q("SELECT label, COUNT(*), COUNT(DISTINCT session || ':' || track_id) "
                       "FROM samples GROUP BY label").fetchall()
        return {label: {"samples": n, "objects": o} for label, n, o in rows}

    def delete_sample(self, sid):
        self._q("DELETE FROM samples WHERE id = ?", (sid,))

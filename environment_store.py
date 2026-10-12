"""新项目独立的采样库，保留数据来源；不读取文物项目数据库。"""
import json
import sqlite3
import math
from contextlib import closing
from pathlib import Path

class EnvironmentStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
                    mode TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS samples_mode_ts ON samples(mode,ts);
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def append(self, sample):
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO samples(ts,mode,payload) VALUES(?,?,?)",
                       (sample["ts"], sample["mode"], json.dumps(sample, ensure_ascii=False, allow_nan=False)))

    def history(self, since, mode, limit=600, until=None):
        until = float("inf") if until is None else until
        with closing(self.connect()) as db:
            count = db.execute("SELECT COUNT(*) FROM samples WHERE ts>=? AND ts<=? AND mode=?",
                               (since, until, mode)).fetchone()[0]
            step = max(1, math.ceil(count / max(1, limit - 2)))
            rows = db.execute("""WITH ordered AS (
                SELECT payload,ROW_NUMBER() OVER (ORDER BY ts,id) AS rn FROM samples
                WHERE ts>=? AND ts<=? AND mode=?
            ) SELECT payload FROM ordered WHERE rn=1 OR rn=? OR rn%?=0 ORDER BY rn""",
                              (since, until, mode, count, step)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def statistics(self, since, until, mode):
        from alert_rule import METRICS
        result = {}
        with closing(self.connect()) as db:
            count = db.execute("SELECT COUNT(*) FROM samples WHERE ts>=? AND ts<=? AND mode=?",
                               (since, until, mode)).fetchone()[0]
            for key in METRICS:
                # Keys come only from the fixed METRICS allowlist.
                row = db.execute(f"""SELECT COUNT(json_extract(payload,'$.{key}')),
                    MIN(json_extract(payload,'$.{key}')),MAX(json_extract(payload,'$.{key}')),
                    AVG(json_extract(payload,'$.{key}')) FROM samples WHERE ts>=? AND ts<=? AND mode=?""",
                                 (since, until, mode)).fetchone()
                result[key] = dict(zip(("valid", "min", "max", "avg"), row))
        return {"total": count, "metrics": result}

    def rows(self, since, until, mode):
        with closing(self.connect()) as db:
            for row in db.execute("SELECT payload FROM samples WHERE ts>=? AND ts<=? AND mode=? ORDER BY ts,id",
                                  (since, until, mode)):
                yield json.loads(row[0])

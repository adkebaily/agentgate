"""Runs and their trials in one SQLite file."""

import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, suite TEXT, started_at TEXT, reps INTEGER);
CREATE TABLE IF NOT EXISTS trials(run_id INTEGER, config TEXT, case_id TEXT, rep INTEGER, status TEXT, data TEXT);
"""


def connect(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def save_run(db, suite_name, reps, rows):
    with db:
        run_id = db.execute(
            "INSERT INTO runs(suite, started_at, reps) VALUES (?, ?, ?)",
            (suite_name, datetime.now(timezone.utc).isoformat(timespec="seconds"), reps),
        ).lastrowid
        db.executemany(
            "INSERT INTO trials VALUES (?, ?, ?, ?, ?, ?)",
            [(run_id, r["config"], r["case_id"], r["rep"], r["status"], json.dumps(r)) for r in rows],
        )
    return run_id


def runs(db):
    return db.execute(
        "SELECT r.id, r.suite, r.started_at, r.reps, COUNT(t.run_id), SUM(t.status = 'pass') "
        "FROM runs r LEFT JOIN trials t ON t.run_id = r.id GROUP BY r.id ORDER BY r.id"
    ).fetchall()


def load_run(db, run_id=None):
    """(run id, suite name, rows) for a run; the latest run when no id is given."""
    run = db.execute(
        "SELECT id, suite FROM runs WHERE id = COALESCE(?, (SELECT MAX(id) FROM runs))", (run_id,)
    ).fetchone()
    if run is None:
        raise SystemExit("no such run; see `agentgate runs`")
    rows = [json.loads(d) for (d,) in db.execute("SELECT data FROM trials WHERE run_id = ? ORDER BY rowid", (run[0],))]
    return run[0], run[1], rows

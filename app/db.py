import sqlite3
import threading

from .config import DATA_DIR

DATA_DIR.mkdir(parents=True, exist_ok=True)
_conn = sqlite3.connect(DATA_DIR / "jobs.db", check_same_thread=False)
_conn.row_factory = sqlite3.Row
_lock = threading.Lock()

with _lock:
    _conn.execute("""CREATE TABLE IF NOT EXISTS jobs(
        id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, system TEXT,
        status TEXT DEFAULT 'queued', detail TEXT DEFAULT '', tracks INTEGER DEFAULT 0)""")
    # Anything that was mid-flight when the container stopped goes back to queued.
    _conn.execute("UPDATE jobs SET status='queued' WHERE status IN ('downloading','converting')")
    _conn.commit()


def run(sql, params=()):
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur


def one(sql, params=()):
    with _lock:
        return _conn.execute(sql, params).fetchone()


def all_(sql, params=()):
    with _lock:
        return _conn.execute(sql, params).fetchall()

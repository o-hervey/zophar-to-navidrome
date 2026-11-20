import sqlite3
import threading

from .config import DATA_DIR

DATA_DIR.mkdir(parents=True, exist_ok=True)
_conn = sqlite3.connect(DATA_DIR / "jobs.db", check_same_thread=False)
_conn.row_factory = sqlite3.Row
_lock = threading.Lock()

def migrate(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS jobs(
        id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, system TEXT,
        status TEXT DEFAULT 'queued', detail TEXT DEFAULT '', tracks INTEGER DEFAULT 0)""")
    # Columns added after the first release: add them to databases created by older versions.
    have = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    for col, ddl in (("session", "TEXT DEFAULT ''"),        # which browser session started the job (view only)
                     ("force", "INTEGER DEFAULT 0"),         # redo: download again and replace the album
                     ("keep_all", "INTEGER DEFAULT 0")):     # redo: keep every track, skip the filters
        if col not in have:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}")
    # Anything that was mid-flight when the container stopped goes back to queued.
    conn.execute("UPDATE jobs SET status='queued' WHERE status IN ('downloading','converting')")
    conn.commit()


with _lock:
    migrate(_conn)


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

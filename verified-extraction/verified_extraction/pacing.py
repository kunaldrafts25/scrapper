"""Cross-process host request slots, backed by the same local SQLite file."""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path


class HostPacer:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS host_pacing(host TEXT PRIMARY KEY, next_at REAL NOT NULL)")

    def reserve(self, host: str, delay: float) -> float:
        """Reserve a request start time; return seconds to wait before sending."""
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            row = db.execute("SELECT next_at FROM host_pacing WHERE host=?", (host,)).fetchone()
            slot = max(now, row[0]) if row else now
            db.execute("INSERT INTO host_pacing(host,next_at) VALUES(?,?) ON CONFLICT(host) DO UPDATE SET next_at=excluded.next_at",
                       (host, slot + max(0.5, delay)))
        return max(0.0, slot - time.time())

"""Cross-process host request slots, backed by the same local SQLite file."""
from __future__ import annotations

import sqlite3
import os
import time
import threading
import uuid
from contextlib import closing
from contextlib import contextmanager
from pathlib import Path

from .security import FetchError


class HostPacer:
    LEASE_SECONDS = 4.0

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS host_pacing(host TEXT PRIMARY KEY, next_at REAL NOT NULL)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(host_pacing)")}
            if "owner" not in columns:
                db.execute("ALTER TABLE host_pacing ADD COLUMN owner TEXT")
            if "lease_until" not in columns:
                db.execute("ALTER TABLE host_pacing ADD COLUMN lease_until REAL NOT NULL DEFAULT 0")

    @contextmanager
    def request(self, host: str, delay: float, deadline: float):
        """Lease one host through DNS, HTTP send, response read, and close."""
        owner = uuid.uuid4().hex
        while True:
            if time.monotonic() >= deadline:
                raise FetchError("DEADLINE", "Job deadline reached while waiting for host")
            with closing(sqlite3.connect(self.path, timeout=0.25)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                now = time.time()
                db.execute("INSERT OR IGNORE INTO host_pacing(host,next_at,owner,lease_until) VALUES(?,0,NULL,0)", (host,))
                next_at, current_owner, lease_until = db.execute(
                    "SELECT next_at,owner,lease_until FROM host_pacing WHERE host=?", (host,)).fetchone()
                if (current_owner is None or lease_until <= now) and next_at <= now:
                    db.execute("UPDATE host_pacing SET owner=?,lease_until=? WHERE host=?",
                               (owner, now + self.LEASE_SECONDS, host))
                    break
                wait = max(0.01, min(0.1, max(next_at - now, lease_until - now if current_owner else 0)))
            time.sleep(min(wait, max(0, deadline - time.monotonic())))
        stop = threading.Event()
        lost = threading.Event()

        def renew():
            while not stop.wait(self.LEASE_SECONDS / 4):
                try:
                    with closing(sqlite3.connect(self.path, timeout=0.5)) as db, db:
                        changed = db.execute("UPDATE host_pacing SET lease_until=? WHERE host=? AND owner=? AND lease_until>?",
                                             (time.time() + self.LEASE_SECONDS, host, owner, time.time())).rowcount
                    if not changed:
                        lost.set()
                        return
                except sqlite3.OperationalError:
                    continue

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()

        def mark_started():
            if lost.is_set():
                raise FetchError("HOST_LEASE_LOST", "Host request lease was lost")
            with closing(sqlite3.connect(self.path, timeout=0.5)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                changed = db.execute("UPDATE host_pacing SET next_at=? WHERE host=? AND owner=? AND lease_until>?",
                                     (time.time() + max(0.5, delay), host, owner, time.time())).rowcount
                if not changed:
                    raise FetchError("HOST_LEASE_LOST", "Host request lease was lost")
                token = os.environ.get("VE_REQUEST_COUNT_TOKEN")
                if token:
                    updated = db.execute("UPDATE request_counts SET started=started+1 WHERE token=?", (token,)).rowcount
                    if updated != 1:
                        raise FetchError("COUNT_LEDGER_LOST", "Request counter was lost")
            return time.time()

        try:
            yield mark_started
        finally:
            stop.set()
            thread.join(timeout=1)
            with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
                db.execute("UPDATE host_pacing SET owner=NULL,lease_until=0 WHERE host=? AND owner=?", (host, owner))

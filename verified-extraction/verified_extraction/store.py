from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs(tenant TEXT NOT NULL, id TEXT NOT NULL, idem TEXT NOT NULL,
                request_hash TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(tenant,id), UNIQUE(tenant,idem));
            CREATE TABLE IF NOT EXISTS snapshots(tenant TEXT NOT NULL, job_id TEXT NOT NULL, hash TEXT NOT NULL,
                html TEXT NOT NULL, PRIMARY KEY(tenant,job_id,hash));
            """)

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def request_hash(request: dict) -> str:
        return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()

    def by_key(self, tenant: str, key: str):
        self.purge_expired()
        with self._db() as db:
            row = db.execute("SELECT request_hash,result FROM jobs WHERE tenant=? AND idem=?", (tenant, key)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def put(self, tenant: str, job_id: str, key: str, request_hash: str, result: dict, snapshots: dict[str, str]):
        with self._db() as db:
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)", (tenant, job_id, key, request_hash, json.dumps(result),
                datetime.now(timezone.utc).isoformat()))
            db.executemany("INSERT INTO snapshots VALUES(?,?,?,?)",
                           [(tenant, job_id, digest, html) for digest, html in snapshots.items()])

    def get(self, tenant: str, job_id: str):
        self.purge_expired()
        with self._db() as db:
            row = db.execute("SELECT result FROM jobs WHERE tenant=? AND id=?", (tenant, job_id)).fetchone()
        return json.loads(row[0]) if row else None

    def snapshot(self, tenant: str, job_id: str, digest: str):
        self.purge_expired()
        with self._db() as db:
            row = db.execute("SELECT html FROM snapshots WHERE tenant=? AND job_id=? AND hash=?", (tenant, job_id, digest)).fetchone()
        return row[0] if row else None

    def delete(self, tenant: str, job_id: str):
        with self._db() as db:
            db.execute("DELETE FROM snapshots WHERE tenant=? AND job_id=?", (tenant, job_id))
            return db.execute("DELETE FROM jobs WHERE tenant=? AND id=?", (tenant, job_id)).rowcount > 0

    def purge_expired(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        with self._db() as db:
            db.execute("DELETE FROM snapshots WHERE (tenant,job_id) IN (SELECT tenant,id FROM jobs WHERE created_at < ?)", (cutoff,))
            db.execute("DELETE FROM jobs WHERE created_at < ?", (cutoff,))

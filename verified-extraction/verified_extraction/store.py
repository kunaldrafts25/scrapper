from __future__ import annotations

import hashlib
import json
import sqlite3
from .fetch import Page
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
            CREATE TABLE IF NOT EXISTS claims(tenant TEXT NOT NULL, idem TEXT NOT NULL,
                request_hash TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(tenant,idem));
            CREATE TABLE IF NOT EXISTS tenant_metrics(tenant TEXT PRIMARY KEY, jobs INTEGER NOT NULL DEFAULT 0,
                pages_fetched INTEGER NOT NULL DEFAULT 0, verified_fields INTEGER NOT NULL DEFAULT 0,
                conflicting_fields INTEGER NOT NULL DEFAULT 0, missing_fields INTEGER NOT NULL DEFAULT 0,
                blocked_fields INTEGER NOT NULL DEFAULT 0, unverified_fields INTEGER NOT NULL DEFAULT 0);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(snapshots)")}
            for column, definition in (("raw", "BLOB"), ("encoding", "TEXT"), ("decoding_errors", "INTEGER")):
                if column not in columns:
                    db.execute(f"ALTER TABLE snapshots ADD COLUMN {column} {definition}")

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

    def claim(self, tenant: str, key: str, request_hash: str) -> str:
        """SQLite's write lock serializes claims across processes sharing this DB."""
        self.purge_expired()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT request_hash,state FROM claims WHERE tenant=? AND idem=?", (tenant, key)).fetchone()
            if row is None:
                db.execute("INSERT INTO claims VALUES(?,?,?,?,?)", (tenant, key, request_hash, "pending",
                    datetime.now(timezone.utc).isoformat()))
                return "owner"
            if row[0] != request_hash:
                return "conflict"
            return row[1]

    def release_claim(self, tenant: str, key: str, request_hash: str):
        with self._db() as db:
            db.execute("DELETE FROM claims WHERE tenant=? AND idem=? AND request_hash=? AND state='pending'",
                       (tenant, key, request_hash))

    def put(self, tenant: str, job_id: str, key: str, request_hash: str, result: dict, snapshots: dict[str, Page | str]):
        with self._db() as db:
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)", (tenant, job_id, key, request_hash, json.dumps(result),
                datetime.now(timezone.utc).isoformat()))
            db.executemany("INSERT INTO snapshots(tenant,job_id,hash,html,raw,encoding,decoding_errors) VALUES(?,?,?,?,?,?,?)",
                           [(tenant, job_id, digest, value.html if isinstance(value, Page) else value,
                             value.raw if isinstance(value, Page) else value.encode("utf-8"),
                             value.encoding if isinstance(value, Page) else "utf-8",
                             value.decoding_errors if isinstance(value, Page) else 0)
                            for digest, value in snapshots.items()])
            db.execute("UPDATE claims SET state='complete' WHERE tenant=? AND idem=? AND request_hash=?",
                       (tenant, key, request_hash))
            counts = {state: sum(field["state"] == state for field in result.get("fields", {}).values())
                      for state in ("verified", "conflicting", "missing", "blocked", "unverified")}
            db.execute("""INSERT INTO tenant_metrics VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(tenant) DO UPDATE SET jobs=jobs+excluded.jobs,
                pages_fetched=pages_fetched+excluded.pages_fetched,
                verified_fields=verified_fields+excluded.verified_fields,
                conflicting_fields=conflicting_fields+excluded.conflicting_fields,
                missing_fields=missing_fields+excluded.missing_fields,
                blocked_fields=blocked_fields+excluded.blocked_fields,
                unverified_fields=unverified_fields+excluded.unverified_fields""",
                (tenant, 1, result.get("usage", {}).get("pages_fetched", 0),
                 counts["verified"], counts["conflicting"], counts["missing"], counts["blocked"], counts["unverified"]))

    def metrics(self, tenant: str) -> dict:
        with self._db() as db:
            row = db.execute("SELECT jobs,pages_fetched,verified_fields,conflicting_fields,missing_fields,blocked_fields,unverified_fields FROM tenant_metrics WHERE tenant=?", (tenant,)).fetchone()
        values = row or (0,) * 7
        return dict(zip(("jobs", "pages_fetched", "verified_fields", "conflicting_fields", "missing_fields", "blocked_fields", "unverified_fields"), values), tenant_id=tenant)

    def get(self, tenant: str, job_id: str):
        self.purge_expired()
        with self._db() as db:
            row = db.execute("SELECT result FROM jobs WHERE tenant=? AND id=?", (tenant, job_id)).fetchone()
        return json.loads(row[0]) if row else None

    def snapshot(self, tenant: str, job_id: str, digest: str):
        record = self.snapshot_record(tenant, job_id, digest)
        return record["html"] if record else None

    def snapshot_record(self, tenant: str, job_id: str, digest: str):
        self.purge_expired()
        with self._db() as db:
            row = db.execute("SELECT html,raw,encoding,decoding_errors FROM snapshots WHERE tenant=? AND job_id=? AND hash=?",
                             (tenant, job_id, digest)).fetchone()
        return dict(zip(("html", "raw", "encoding", "decoding_errors"), row)) if row else None

    def delete(self, tenant: str, job_id: str):
        with self._db() as db:
            db.execute("DELETE FROM claims WHERE tenant=? AND idem IN (SELECT idem FROM jobs WHERE tenant=? AND id=?)",
                       (tenant, tenant, job_id))
            db.execute("DELETE FROM snapshots WHERE tenant=? AND job_id=?", (tenant, job_id))
            return db.execute("DELETE FROM jobs WHERE tenant=? AND id=?", (tenant, job_id)).rowcount > 0

    def purge_expired(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        with self._db() as db:
            db.execute("DELETE FROM snapshots WHERE (tenant,job_id) IN (SELECT tenant,id FROM jobs WHERE created_at < ?)", (cutoff,))
            db.execute("DELETE FROM jobs WHERE created_at < ?", (cutoff,))
            db.execute("DELETE FROM claims WHERE created_at < ?", (cutoff,))

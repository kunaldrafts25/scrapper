import uuid
import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from verified_extraction import api
from verified_extraction.extract import verify_candidate, page_fact_candidates
from verified_extraction.fetch import Page
from verified_extraction.service import run_job
from verified_extraction.models import JobRequest
from verified_extraction.store import Store


HTML = '''<html><head><title>Example guide</title>
<meta name="description" content="A concise guide to the example.">
<meta property="og:site_name" content="Example Library"></head>
<body><h1>Getting started</h1><h1 hidden>Hidden heading</h1></body></html>'''


def test_quick_job_needs_local_opt_in_and_returns_bound_page_facts(monkeypatch):
    path = Path(f"test-{uuid.uuid4().hex}.db")
    monkeypatch.setattr(api, "store", Store(str(path)))
    monkeypatch.delenv("VE_LOCAL_UI", raising=False)
    monkeypatch.delenv("VE_KEYS", raising=False)

    class Fetcher:
        def fetch(self, url):
            return Page(url, HTML, "2026-09-30T00:00:00+00:00", [])

    monkeypatch.setattr(api, "run_hard", lambda request, on_tick=None: run_job(request, Fetcher()))
    client = TestClient(api.app, client=("127.0.0.1", 41234), base_url="http://127.0.0.1")
    try:
        assert "Paste a link. Get the facts." in client.get("/").text
        payload = {"url": "https://example.org/guide"}
        assert client.post("/v1/quick-jobs", json=payload).status_code == 401
        monkeypatch.setenv("VE_LOCAL_UI", "1")
        assert client.post("/v1/quick-jobs", json=payload,
                           headers={"Origin": "https://evil.example"}).status_code == 401
        remote = TestClient(api.app, client=("198.51.100.1", 41234), base_url="http://127.0.0.1")
        assert remote.post("/v1/quick-jobs", json=payload,
                           headers={"Origin": "http://127.0.0.1"}).status_code == 401
        assert client.post("/v1/quick-jobs", json=payload).status_code == 401
        result = client.post("/v1/quick-jobs", json=payload,
                             headers={"Origin": "http://127.0.0.1"})
        assert result.status_code == 200
        fields = result.json()["fields"]
        automatic = result.json()["automatic_result"]
        assert automatic["version"] == "1.0"
        assert automatic["overview"]["state"] == "found_in_source"
        assert automatic["overview"]["text"] == "A concise guide to the example."
        assert automatic["facts"]
        assert {name: field["value"] for name, field in fields.items()} == {
            "page_title": "Example guide", "main_heading": "Getting started",
            "page_description": "A concise guide to the example.", "site_name": "Example Library"}
        assert all(field["state"] == "verified" and field["evidence"] for field in fields.values())
        assert client.get(f'/v1/jobs/{result.json()["job_id"]}').status_code == 200
        assert client.get(f'/v1/jobs/{result.json()["job_id"]}',
                          headers={"Host": "evil.example"}).status_code == 401
        recent = client.get("/v1/recent-jobs").json()["jobs"]
        assert [item["job_id"] for item in recent] == [result.json()["job_id"]]
        assert client.delete(f'/v1/jobs/{result.json()["job_id"]}',
                             headers={"Origin": "http://127.0.0.1"}).json() == {"deleted": True}
        assert client.get("/v1/recent-jobs").json()["jobs"] == []
        assert client.get(f'/v1/jobs/{result.json()["job_id"]}').status_code == 404
        assert api.store.claim("__local_ui__", "pending", "hash", "owner") == "owner"
        cancelled = client.post("/v1/quick-jobs/cancel", json={"idempotency_key": "pending"},
                                headers={"Origin": "http://127.0.0.1"})
        assert cancelled.json() == {"cancelled": True}
    finally:
        path.unlink(missing_ok=True)


def test_page_fact_rejects_hidden_heading_and_changed_capture():
    page = Page("https://example.org/guide", HTML, "now", [])
    candidates = page_fact_candidates(page, "main_heading", {"type": "string"})
    assert len(candidates) == 1
    assert verify_candidate(candidates[0], page)
    changed = Page(page.url, HTML.replace("Getting started", "Changed"), page.fetched_at, [])
    assert not verify_candidate(candidates[0], changed)


def test_cancel_claim_stops_same_tenant_and_is_not_retried():
    path = Path(f"test-{uuid.uuid4().hex}.db")
    try:
        store = Store(str(path))
        assert store.claim("tenant-a", "quick-1", "hash", "owner") == "owner"
        assert not store.cancel_claim("tenant-b", "quick-1")
        assert store.cancel_claim("tenant-a", "quick-1")
        assert store.claim("tenant-a", "quick-1", "hash", "next-owner") == "cancelled"
        assert not store.cancel_claim("tenant-a", "quick-1")
    finally:
        path.unlink(missing_ok=True)


def test_expiry_removes_capture_and_review_records():
    path = Path(f"test-{uuid.uuid4().hex}.db")
    store = Store(str(path))
    request = JobRequest(url="https://example.org/guide", schema={"type": "object", "properties": {
        "page_title": {"type": "string", "title": "Page title"},
        "main_heading": {"type": "string", "title": "Main heading"},
        "page_description": {"type": "string", "title": "Description"}}},
        idempotency_key="retention")

    class Fetcher:
        def fetch(self, url):
            return Page(url, HTML, "now", [])

    result, captures = run_job(request, Fetcher())
    store.put("local", result.job_id, "retention", "hash", result.model_dump(), captures)
    store.start_review("local", result.job_id, "page_title", "reviewer")
    store.stop_review("local", result.job_id, "page_title", "reviewer")
    store.save_review("local", result.job_id, "page_title", {"reviewer_id": "reviewer", "verdict": "correct"})
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("UPDATE jobs SET created_at=?", (old,))
    store.purge_expired()
    assert store.get("local", result.job_id) is None
    assert store.snapshot("local", result.job_id, next(iter(captures))) is None
    assert store.reviews("local", result.job_id) == {}
    assert store.review_sessions("local", result.job_id) == []
    path.unlink()


def test_concurrent_store_startup_serializes_migrations():
    path = Path(f"test-{uuid.uuid4().hex}.db")
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            stores = list(pool.map(lambda _: Store(str(path)), range(4)))
        assert all(item.recent("local") == [] for item in stores)
    finally:
        path.unlink(missing_ok=True)

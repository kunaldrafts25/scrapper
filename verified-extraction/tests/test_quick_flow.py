import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from verified_extraction import api
from verified_extraction.extract import verify_candidate, page_fact_candidates
from verified_extraction.fetch import Page
from verified_extraction.service import run_job
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
        assert {name: field["value"] for name, field in fields.items()} == {
            "page_title": "Example guide", "main_heading": "Getting started",
            "page_description": "A concise guide to the example.", "site_name": "Example Library"}
        assert all(field["state"] == "verified" and field["evidence"] for field in fields.values())
        assert client.get(f'/v1/jobs/{result.json()["job_id"]}').status_code == 200
        assert client.get(f'/v1/jobs/{result.json()["job_id"]}',
                          headers={"Host": "evil.example"}).status_code == 401
    finally:
        path.unlink(missing_ok=True)


def test_page_fact_rejects_hidden_heading_and_changed_capture():
    page = Page("https://example.org/guide", HTML, "now", [])
    candidates = page_fact_candidates(page, "main_heading", {"type": "string"})
    assert len(candidates) == 1
    assert verify_candidate(candidates[0], page)
    changed = Page(page.url, HTML.replace("Getting started", "Changed"), page.fetched_at, [])
    assert not verify_candidate(candidates[0], changed)

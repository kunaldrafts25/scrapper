import re
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from verified_extraction import api
from verified_extraction.fetch import Page
from verified_extraction.service import run_job
from verified_extraction.store import Store


def test_workbench_submit_review_export_and_api_error(monkeypatch):
    path = Path(f"test-{uuid.uuid4().hex}.db")
    monkeypatch.setattr(api, "store", Store(str(path)))
    monkeypatch.setenv("VE_KEYS", '{"owner":"owner-key","other":"other-key"}')

    class Fetcher:
        def fetch(self, url):
            return Page(url, "<p>Plan: Team</p><p>Price: $29</p><p>Support: Email</p>", "now", [])

    monkeypatch.setattr(api, "run_hard", lambda request, on_tick=None: run_job(request, Fetcher()))
    client = TestClient(api.app)
    page = client.get("/review")
    assert page.status_code == 200
    assert "New extraction" in page.text and "Open a job" in page.text
    assert all(id_ in page.text for id_ in ("create-form", "open-form", "reviewer", "page-errors", "export"))
    assert "textContent" in page.text and "innerHTML=" not in page.text
    assert "<script src=" not in page.text and "__NONCE__" not in page.text
    nonce = re.search(r'<script nonce="([^"]+)"', page.text).group(1)
    csp = page.headers["content-security-policy"]
    assert f"script-src 'nonce-{nonce}'" in csp
    assert f"style-src 'nonce-{nonce}'" in csp
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp
    assert page.headers["cache-control"] == "no-store"

    owner = {"Authorization": "Bearer owner-key"}
    payload = {"url": "https://example.org/pricing", "schema": {"type": "object", "properties": {
        "plan": {"type": "string", "title": "Plan"},
        "price": {"type": "number", "title": "Price"},
        "support": {"type": "string", "title": "Support"}}},
        "options": {"max_pages": 1, "max_depth": 0, "deadline_seconds": 10, "max_http_requests": 5},
        "idempotency_key": "workbench-flow"}
    created = client.post("/v1/jobs", json=payload, headers=owner)
    assert created.status_code == 200
    job = created.json()["job_id"]
    assert client.get(f"/v1/jobs/{job}", headers=owner).json()["fields"]["support"]["value"] == "Email"
    assert client.get(f"/v1/jobs/{job}/review-evidence", headers=owner).json()["support"][0]["source_bound"]
    assert client.post(f"/v1/jobs/{job}/reviews/support/start", json={"reviewer_id": "analyst"}, headers=owner).status_code == 200
    assert client.post(f"/v1/jobs/{job}/reviews/support/stop", json={"reviewer_id": "analyst"}, headers=owner).status_code == 200
    assert client.put(f"/v1/jobs/{job}/reviews/support", json={"reviewer_id": "analyst", "verdict": "correct"}, headers=owner).status_code == 200
    exported = client.get(f"/v1/jobs/{job}/labels", headers=owner).json()
    assert exported["reviews"]["support"]["verdict"] == "correct"
    assert len(exported["review_history"]) == 1
    denied = client.get(f"/v1/jobs/{job}", headers={"Authorization": "Bearer wrong-key"})
    assert denied.status_code == 401 and denied.json()["detail"]["code"] == "UNAUTHORIZED"
    assert client.get(f"/v1/jobs/{job}/labels", headers={"Authorization": "Bearer other-key"}).status_code == 404
    path.unlink()

import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from verified_extraction import api
from verified_extraction.fetch import Page
from verified_extraction.service import run_job
from verified_extraction.store import Store
import pytest


def test_reviewer_labels_export_and_tenant_isolation(monkeypatch):
    path = Path(f"test-{uuid.uuid4().hex}.db")
    monkeypatch.setattr(api, "store", Store(str(path)))
    monkeypatch.setenv("VE_KEYS", '{"a":"secret-a","b":"secret-b"}')
    class Fetcher:
        def fetch(self, url):
            return Page(url, '<p>Support: Email</p><p>Plan price: $29/month</p><p>Usage limit: 100</p><script>alert(1)</script>', "now", [])
    monkeypatch.setattr(api, "run_hard", lambda request, on_tick=None: run_job(request, Fetcher()))
    client = TestClient(api.app)
    a = {"Authorization": "Bearer secret-a"}
    b = {"Authorization": "Bearer secret-b"}
    body = {"url": "https://example.com/", "schema": {"type": "object", "properties": {
        "support": {"type": "string"}, "plan_price": {"type": "number"},
        "usage_limit": {"type": "integer"}}}, "idempotency_key": "review-1",
        "options": {"max_pages": 1, "max_depth": 0, "deadline_seconds": 10}}
    result = client.post("/v1/jobs", json=body, headers=a).json()
    job_id = result["job_id"]
    ticks = iter([100, 145, 200, 220, 300, 310, 400, 405])
    api.store.clock = lambda: next(ticks)
    review = {"verdict": "correct", "reason": "Exact visible label", "reviewer_id": "analyst-1",
              "corrected_value": None, "corrected_source_url": None, "corrected_excerpt": None}
    session = {"reviewer_id": "analyst-1"}
    assert client.post(f"/v1/jobs/{job_id}/reviews/support/start", json=session, headers=a).status_code == 200
    assert client.post(f"/v1/jobs/{job_id}/reviews/plan_price/start", json=session, headers=a).status_code == 409
    assert client.post(f"/v1/jobs/{job_id}/reviews/support/stop", json=session, headers=a).json()["elapsed_seconds"] == 45
    assert client.put(f"/v1/jobs/{job_id}/reviews/support", json=review, headers=a).status_code == 200
    for field, seconds in (("plan_price", 20), ("usage_limit", 10)):
        assert client.post(f"/v1/jobs/{job_id}/reviews/{field}/start", json=session, headers=a).status_code == 200
        assert client.post(f"/v1/jobs/{job_id}/reviews/{field}/stop", json=session, headers=a).json()["elapsed_seconds"] == seconds
        assert client.put(f"/v1/jobs/{job_id}/reviews/{field}", json=review, headers=a).status_code == 200
    assert client.put(f"/v1/jobs/{job_id}/reviews/support", json=review, headers=b).status_code == 404
    assert client.get(f"/v1/jobs/{job_id}/reviews", headers=b).status_code == 404
    assert client.get(f"/v1/jobs/{job_id}/labels", headers=b).status_code == 404
    labels = client.get(f"/v1/jobs/{job_id}/labels", headers=a).json()
    assert labels["label_schema_version"] == "1.1"
    assert labels["machine_result"] == result
    assert labels["reviews"]["support"]["verdict"] == "correct"
    assert labels["review_minutes_total"] == 1.25
    assert labels["review_minutes_per_accepted_field"] == 0.4167
    assert len(labels["review_sessions"]) == 3
    assert sum(row["elapsed_seconds"] for row in labels["review_sessions"]) == 75
    assert client.post(f"/v1/jobs/{job_id}/reviews/support/start", json=session, headers=a).status_code == 200
    assert client.post(f"/v1/jobs/{job_id}/reviews/support/stop", json=session, headers=a).json()["elapsed_seconds"] == 5
    edited = {**review, "verdict": "wrong", "reason": "Second inspection"}
    assert client.put(f"/v1/jobs/{job_id}/reviews/support", json=edited, headers=a).json()["time_spent_seconds"] == 50
    history = client.get(f"/v1/jobs/{job_id}/review-history", headers=a).json()
    assert len(history["edits"]) == 4 and len(history["sessions"]) == 4
    assert history["edits"][0]["verdict"] == "correct" and history["edits"][-1]["verdict"] == "wrong"
    assert client.get(f"/v1/jobs/{job_id}/labels", headers=a).json()["review_minutes_total"] == 1.3333
    evidence = client.get(f"/v1/jobs/{job_id}/review-evidence", headers=a).json()["support"][0]
    assert evidence["source_bound"] and evidence["source_node"] == "<p>Support: Email</p>"
    assert client.get(f"/v1/jobs/{job_id}", headers=a).json() == result
    page = client.get("/review").text
    assert "textContent" in page and "Source node:" in page
    assert "secret-a" not in page and "<script>alert(1)</script>" not in page
    assert client.delete(f"/v1/jobs/{job_id}", headers=a).status_code == 200
    assert client.get(f"/v1/jobs/{job_id}/labels", headers=a).status_code == 404
    path.unlink()


def test_reviewer_cannot_overlap_sessions_across_jobs():
    path = Path(f"test-{uuid.uuid4().hex}.db")
    store = Store(str(path))
    store.clock = iter([10, 15, 20]).__next__
    try:
        store.start_review("tenant", "job-a", "support", "analyst")
        with pytest.raises(ValueError, match="active"):
            store.start_review("tenant", "job-b", "price", "analyst")
        assert store.stop_review("tenant", "job-a", "support", "analyst")["elapsed_seconds"] == 5
        store.start_review("tenant", "job-b", "price", "analyst")
    finally:
        path.unlink()

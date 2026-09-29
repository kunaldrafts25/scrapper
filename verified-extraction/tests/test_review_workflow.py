import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from verified_extraction import api
from verified_extraction.fetch import Page
from verified_extraction.service import run_job
from verified_extraction.store import Store


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
    review = {"verdict": "correct", "reason": "Exact visible label", "time_spent_seconds": 45,
              "corrected_value": None, "corrected_source_url": None, "corrected_excerpt": None}
    assert client.put(f"/v1/jobs/{job_id}/reviews/support", json=review, headers=a).status_code == 200
    assert client.put(f"/v1/jobs/{job_id}/reviews/support", json=review, headers=b).status_code == 404
    assert client.get(f"/v1/jobs/{job_id}/reviews", headers=b).status_code == 404
    assert client.get(f"/v1/jobs/{job_id}/labels", headers=b).status_code == 404
    labels = client.get(f"/v1/jobs/{job_id}/labels", headers=a).json()
    assert labels["label_schema_version"] == "1.0"
    assert labels["machine_result"] == result
    assert labels["reviews"]["support"]["verdict"] == "correct"
    assert labels["review_minutes_per_accepted_field"] == 0.75
    evidence = client.get(f"/v1/jobs/{job_id}/review-evidence", headers=a).json()["support"][0]
    assert evidence["source_bound"] and evidence["source_node"] == "<p>Support: Email</p>"
    assert client.get(f"/v1/jobs/{job_id}", headers=a).json() == result
    page = client.get("/review").text
    assert "textContent" in page and "Source node:" in page
    assert "secret-a" not in page and "<script>alert(1)</script>" not in page
    assert client.delete(f"/v1/jobs/{job_id}", headers=a).status_code == 200
    assert client.get(f"/v1/jobs/{job_id}/labels", headers=a).status_code == 404
    path.unlink()

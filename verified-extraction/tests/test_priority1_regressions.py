"""Each test captures a bug in the original local MVP."""
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from verified_extraction import api
from verified_extraction.extract import extract_fields
from verified_extraction.fetch import HTTPFetcher, Page
from verified_extraction.models import JobRequest
from verified_extraction.security import FetchError
from verified_extraction.service import run_job
from verified_extraction.store import Store

SCHEMA = {"type": "object", "properties": {"plan_price": {"type": "number"},
    "usage_limit": {"type": "integer"}, "support": {"type": "string"}}}


def job(**kwargs):
    return JobRequest.model_validate({"url": "https://example.com/", "schema": SCHEMA,
        "idempotency_key": "same", **kwargs})


@pytest.mark.parametrize("hidden", [
    "<p hidden>Support: Secret</p>",
    "<div hidden><p>Support: Secret</p></div>",
    '<div aria-hidden="true"><p>Support: Secret</p></div>',
    '<div style="visibility: hidden !important"><p>Support: Secret</p></div>',
    '<div style="opacity:0"><p>Support: Secret</p></div>',
])
def test_hidden_value_does_not_conflict_with_visible(hidden):
    page = Page("https://example.com/", hidden + "<p>Support: Email</p>", "now", [])
    field = extract_fields([page], SCHEMA["properties"], False)["support"]
    assert field.state == "verified" and field.value == "Email"


@pytest.mark.parametrize("status", [201, 400, 410, 451, 500])
def test_robots_unexpected_status_fails_closed(monkeypatch, status):
    fetcher = HTTPFetcher({"example.com"}, time.monotonic() + 10)
    requested = []
    def response(url):
        requested.append(url)
        return (status, {}, b"") if url.endswith("robots.txt") else (200, {"content-type": "text/html"}, b"ok")
    monkeypatch.setattr(fetcher, "_request", response)
    with pytest.raises(FetchError) as error:
        fetcher.fetch("https://example.com/")
    assert error.value.code == "ROBOTS_UNAVAILABLE"
    assert requested == ["https://example.com/robots.txt"]


def test_depth_zero_ignores_hints():
    calls = []
    class Fetcher:
        def fetch(self, url):
            calls.append(url)
            return Page(url, "<p>Support: Email</p>", "now", [])
    run_job(job(page_hints=["/pricing"], options={"max_pages": 3, "max_depth": 0, "deadline_seconds": 10}), Fetcher())
    assert calls == ["https://example.com/"]


@pytest.mark.parametrize("bad", ["support", ["not_a_field"], [3], {"support": True}])
def test_bad_required_is_structured_422(monkeypatch, bad):
    monkeypatch.setenv("VE_KEYS", '{"a":"secret-a"}')
    body = job().model_dump(by_alias=True)
    body["schema"]["required"] = bad
    response = TestClient(api.app).post("/v1/jobs", json=body, headers={"Authorization": "Bearer secret-a"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("bad", [3, "", [], {"x": 1}])
def test_bad_title_is_structured_422(monkeypatch, bad):
    monkeypatch.setenv("VE_KEYS", '{"a":"secret-a"}')
    body = job().model_dump(by_alias=True)
    body["schema"]["properties"]["support"]["title"] = bad
    response = TestClient(api.app).post("/v1/jobs", json=body, headers={"Authorization": "Bearer secret-a"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"


def test_concurrent_idempotency_one_crawl(monkeypatch):
    path = Path(f"test-{uuid.uuid4().hex}.db")
    monkeypatch.setattr(api, "store", Store(str(path)))
    monkeypatch.setenv("VE_KEYS", '{"a":"secret-a"}')
    count = 0
    guard = threading.Lock()
    class Fetcher:
        def fetch(self, url):
            nonlocal count
            with guard:
                count += 1
            time.sleep(0.15)
            return Page(url, "<p>Support: Email</p>", "now", [])
    monkeypatch.setattr(api, "run_job", lambda request: run_job(request, Fetcher()))
    body = job(options={"max_pages": 1, "max_depth": 0, "deadline_seconds": 10}).model_dump(by_alias=True)
    def send(_):
        return TestClient(api.app).post("/v1/jobs", json=body, headers={"Authorization": "Bearer secret-a"})
    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(send, range(5)))
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["job_id"] for response in responses}) == 1
    assert count == 1
    path.unlink()


def test_metrics_are_tenant_scoped(monkeypatch):
    monkeypatch.setenv("VE_KEYS", '{"a":"secret-a","b":"secret-b"}')
    client = TestClient(api.app)
    a = client.get("/v1/metrics", headers={"Authorization": "Bearer secret-a"})
    b = client.get("/v1/metrics", headers={"Authorization": "Bearer secret-b"})
    assert a.status_code == b.status_code == 200
    assert a.json().get("tenant_id") == "a"
    assert b.json().get("tenant_id") == "b"

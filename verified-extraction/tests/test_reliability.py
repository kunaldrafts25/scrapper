import os
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from verified_extraction.fetch import Page
from verified_extraction.models import JobRequest
from verified_extraction.pacing import HostPacer
from verified_extraction.security import FetchError
from verified_extraction.service import run_job
from verified_extraction.store import Store
from verified_extraction.worker import run_hard


class SlowFetcher:
    def fetch(self, url):
        time.sleep(10)
        return Page(url, "<p>Support: Email</p>", "now", [])


def slow_job(request):
    return run_job(request, SlowFetcher())


def interrupted_job(request):
    os._exit(7)


def quick_job(request):
    class Fetcher:
        def fetch(self, url):
            return Page(url, "<p>Support: Email</p>", "now", [])
    return run_job(request, Fetcher())


def request():
    return JobRequest.model_validate({"url": "https://example.com/", "schema": {"type": "object",
        "properties": {"support": {"type": "string"}, "plan_price": {"type": "number"},
                       "usage_limit": {"type": "integer"}}}, "idempotency_key": "one",
        "options": {"max_pages": 1, "max_depth": 0, "deadline_seconds": 2}})


def test_hard_deadline_kills_slow_response():
    start = time.monotonic()
    with pytest.raises(FetchError) as error:
        run_hard(request(), work="tests.test_reliability:slow_job")
    elapsed = time.monotonic() - start
    assert error.value.code == "DEADLINE"
    assert 1.8 <= elapsed < 4.0


def test_interrupted_worker_returns_typed_error():
    start = time.monotonic()
    with pytest.raises(FetchError) as error:
        run_hard(request(), work="tests.test_reliability:interrupted_job")
    assert error.value.code == "WORKER_FAILED"
    assert time.monotonic() - start < 2.0


def test_child_returns_capture_and_renews_lease():
    calls = []
    result, captures = run_hard(request(), work="tests.test_reliability:quick_job",
                                on_tick=lambda: calls.append(time.monotonic()) or True)
    assert result.fields["support"].state == "verified"
    assert len(captures) == 1
    assert next(iter(captures.values())).raw == b"<p>Support: Email</p>"


def test_stale_claim_recovery_and_old_owner_cannot_complete():
    path = Path(f"test-{uuid.uuid4().hex}.db")
    first, second = Store(str(path)), Store(str(path))
    first.LEASE_SECONDS = second.LEASE_SECONDS = 0.25
    start = time.monotonic()
    assert first.claim("tenant", "key", "payload", "owner-a") == "owner"
    time.sleep(0.3)
    assert second.claim("tenant", "key", "payload", "owner-b") == "owner"
    assert second.claim("tenant", "key", "changed", "owner-c") == "conflict"
    with pytest.raises(RuntimeError, match="CLAIM_LOST"):
        first.put("tenant", "old", "key", "payload", {"fields": {}, "usage": {}}, {}, owner="owner-a")
    first.release_claim("tenant", "key", "payload", "owner-a")
    second.put("tenant", "new", "key", "payload", {"fields": {}, "usage": {}}, {}, owner="owner-b")
    assert second.by_key("tenant", "key")[1]["fields"] == {}
    assert time.monotonic() - start < 2.0
    path.unlink()


def test_claim_heartbeat_prevents_takeover():
    path = Path(f"test-{uuid.uuid4().hex}.db")
    first, second = Store(str(path)), Store(str(path))
    first.LEASE_SECONDS = second.LEASE_SECONDS = 0.35
    assert first.claim("tenant", "key", "payload", "owner-a") == "owner"
    time.sleep(0.2)
    assert first.renew_claim("tenant", "key", "payload", "owner-a")
    time.sleep(0.2)
    assert second.claim("tenant", "key", "payload", "owner-b") == "pending"
    first.release_claim("tenant", "key", "payload", "owner-a")
    path.unlink()


def test_cross_process_host_requests_do_not_overlap_and_space_actual_starts():
    path = Path(f"test-pacing-{uuid.uuid4().hex}.db")
    files = []
    try:
        _check_cross_process_pacing(path, files)
    finally:
        for file in files:
            file.unlink(missing_ok=True)
        path.unlink(missing_ok=True)


def _check_cross_process_pacing(path, files):
    def pair(hold, delay):
        pair_files = [Path(f"test-pacing-{uuid.uuid4().hex}.json") for _ in range(2)]
        files.extend(pair_files)
        processes = [subprocess.Popen([sys.executable, "-m", "tests.offline_host_worker", str(path), str(file),
                         str(delay), str(hold)]) for file in pair_files]
        for process in processes:
            assert process.wait(timeout=8) == 0
        first, second = sorted((json.loads(file.read_text()) for file in pair_files), key=lambda row: row["start"])
        assert second["start"] >= first["finish"] - 0.005
        assert second["start"] - first["start"] >= max(0.5, delay) - 0.03
    pair(0.65, 0.2)
    pair(0.04, 0.8)


def test_dead_worker_host_lease_recovers():
    path = Path(f"test-pacing-{uuid.uuid4().hex}.db")
    try:
        _check_dead_worker_recovery(path)
    finally:
        path.unlink(missing_ok=True)


def _check_dead_worker_recovery(path):
    worker = ("import sys,time; from verified_extraction.pacing import HostPacer; "
              "HostPacer.LEASE_SECONDS=.5; p=HostPacer(sys.argv[1])\n"
              "with p.request('example.com',.1,time.monotonic()+5) as started:\n"
              "  started(); print('READY',flush=True); time.sleep(10)")
    process = subprocess.Popen([sys.executable, "-c", worker, str(path)], stdout=subprocess.PIPE)
    try:
        assert process.stdout.readline().strip() == b"READY"
        process.kill()
        process.wait(timeout=3)
        pacer = HostPacer(str(path))
        pacer.LEASE_SECONDS = 0.5
        before = time.monotonic()
        with pacer.request("example.com", 0.1, before + 3) as started:
            started()
        elapsed = time.monotonic() - before
        assert 0.2 <= elapsed < 2
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_http_budget_counts_robots_and_redirect_requests_offline(monkeypatch):
    from verified_extraction import fetch
    path = Path(f"test-pacing-{uuid.uuid4().hex}.db")
    monkeypatch.setenv("VE_DB", str(path))
    monkeypatch.setattr(fetch, "resolve_public", lambda host, port: "203.0.113.1")
    calls = []
    class Response:
        status = 200
        def getheaders(self):
            return [("Content-Type", "text/html")]
        def read(self, size):
            return b"ok"
    class Connection:
        def __init__(self, *args):
            pass
        def connect(self):
            pass
        def request(self, method, path, headers):
            calls.append(path)
        def getresponse(self):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(fetch, "PinnedHTTPS", Connection)
    try:
        agent = fetch.HTTPFetcher({"example.com"}, time.monotonic() + 3, max_http_requests=1)
        agent._request("https://example.com/robots.txt")
        with pytest.raises(FetchError, match="ceiling") as error:
            agent._request("https://example.com/page")
        assert error.value.code == "REQUEST_BUDGET"
        assert calls == ["/robots.txt"]
        assert agent.http_requests_started == 1
    finally:
        path.unlink(missing_ok=True)

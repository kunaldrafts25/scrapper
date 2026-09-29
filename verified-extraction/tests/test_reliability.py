import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
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


def test_pacing_reservations_coordinate_separate_instances():
    path = Path(f"test-{uuid.uuid4().hex}.db")
    a, b = HostPacer(str(path)), HostPacer(str(path))
    with ThreadPoolExecutor(max_workers=2) as pool:
        waits = list(pool.map(lambda pacer: pacer.reserve("example.com", 1.25), (a, b)))
    assert min(waits) < 0.2
    assert max(waits) >= 1.0
    assert b.reserve("other.example", 0.5) < 0.2
    path.unlink()

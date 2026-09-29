"""Offline measurement of hard deadline and stale-claim recovery."""
import json
import time
import uuid
from pathlib import Path

from verified_extraction.models import JobRequest
from verified_extraction.security import FetchError
from verified_extraction.store import Store
from verified_extraction.worker import run_hard


def slow_job(request):
    time.sleep(10)


def main():
    request = JobRequest.model_validate({"url": "https://example.com/", "schema": {"type": "object",
        "properties": {"support": {"type": "string"}, "plan_price": {"type": "number"},
                       "usage_limit": {"type": "integer"}}}, "idempotency_key": "measure",
        "options": {"max_pages": 1, "max_depth": 0, "deadline_seconds": 2}})
    start = time.monotonic()
    try:
        run_hard(request, work="benchmark.measure_reliability:slow_job")
        deadline_code = "unexpected_success"
    except FetchError as exc:
        deadline_code = exc.code
    elapsed = time.monotonic() - start
    path = Path(f"measurement-{uuid.uuid4().hex}.db")
    try:
        first, second = Store(str(path)), Store(str(path))
        first.LEASE_SECONDS = second.LEASE_SECONDS = 0.5
        recovery_start = time.monotonic()
        first.claim("measurement", "key", "payload", "dead-owner")
        while time.monotonic() - recovery_start < 0.55:
            time.sleep(0.01)
        recovery_state = second.claim("measurement", "key", "payload", "new-owner")
        recovery_elapsed = time.monotonic() - recovery_start
    finally:
        path.unlink(missing_ok=True)
    print(json.dumps({"network_calls": 0, "deadline_seconds": 2, "deadline_code": deadline_code,
        "observed_elapsed_seconds": round(elapsed, 4), "observed_overshoot_seconds": round(elapsed - 2, 4),
        "claim_lease_seconds": 0.5, "recovery_state": recovery_state,
        "observed_recovery_seconds": round(recovery_elapsed, 4)}, indent=2))


if __name__ == "__main__":
    main()

import pytest

from verified_extraction.models import JobRequest
from verified_extraction.security import FetchError
from verified_extraction.service import run_job
from benchmark.run import summarize


@pytest.mark.parametrize("code", ["ROBOTS_DENIED", "ROBOTS_UNAVAILABLE", "PRIVATE_TARGET", "OUT_OF_SCOPE"])
def test_access_policy_stop_does_not_fetch_queued_hint(code):
    request = JobRequest.model_validate({"url": "https://vendor.example/",
        "schema": {"type": "object", "properties": {"plan": {"type": "string"},
            "price": {"type": "number"}, "support": {"type": "string"}}},
        "idempotency_key": "policy-stop", "page_hints": ["/pricing"],
        "options": {"max_pages": 2, "max_depth": 1, "deadline_seconds": 10}})

    class DeniedFetcher:
        calls = []

        def fetch(self, url):
            self.calls.append(url)
            raise FetchError(code, "Stopped by access policy")

    fetcher = DeniedFetcher()
    result, captures = run_job(request, fetcher)
    assert fetcher.calls == ["https://vendor.example/"]
    assert captures == {}
    assert result.errors[0]["code"] == code
    assert all(field.state == "blocked" for field in result.fields.values())


def test_replay_reports_preflight_and_job_gets_together():
    row = {"latency_seconds": 0.1, "live_elapsed_seconds": 1.0,
           "http_requests_started": 3, "preflight_http_requests_started": 2,
           "failure": None, "access_failure_codes": [], "fields": {},
           "reviewed_row_correct": False}
    summary = summarize([row])
    assert summary["http_requests_started"] == 3
    assert summary["preflight_http_requests_started"] == 2
    assert summary["total_http_requests_started"] == 5

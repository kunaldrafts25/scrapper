"""Source integrity failures pinned against the 0.2 extractor."""
import pytest

from verified_extraction.extract import candidates_for, extract_fields, verify_candidate
from verified_extraction.fetch import Page, decode_html
from verified_extraction.models import JobRequest
from verified_extraction.service import run_job


def test_large_integer_is_exact():
    page = Page("https://example.com/", "<p>Usage limit: 9007199254740993</p>", "now", [])
    field = extract_fields([page], {"usage_limit": {"type": "integer"}}, False)["usage_limit"]
    assert field.state == "verified"
    assert field.value == 9007199254740993


def test_identical_bytes_keep_both_source_urls():
    html = '<p>Support: Email</p><a href="/copy?utm_source=a">A</a><a href="/copy?utm_source=b">B</a>'
    class Fetcher:
        def fetch(self, url):
            return Page(url, html, "now", [])
    schema = {"type": "object", "properties": {"support": {"type": "string"},
        "plan_price": {"type": "number"}, "usage_limit": {"type": "integer"}}}
    request = JobRequest.model_validate({"url": "https://example.com/", "schema": schema,
        "idempotency_key": "two", "options": {"max_pages": 3, "max_depth": 1, "deadline_seconds": 10}})
    result, captures = run_job(request, Fetcher())
    evidence = result.fields["support"].evidence
    assert {item.source_url for item in evidence} == {"https://example.com/", "https://example.com/copy?utm_source=a", "https://example.com/copy?utm_source=b"}
    assert len(captures) == 1


def test_legacy_bytes_require_encoding_bearing_capture():
    raw = "<p>Support: Café</p>".encode("cp1252")
    html, encoding, errors = decode_html(raw, "text/html; charset=windows-1252")
    page = Page("https://example.com/", html, "now", [], raw, encoding, errors)
    candidate = candidates_for(page, "support", {"type": "string"})[0]
    assert verify_candidate(candidate, page)
    with pytest.raises(TypeError, match="Page"):
        verify_candidate(candidate, raw)

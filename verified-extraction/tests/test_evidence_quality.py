import hashlib
import json
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from verified_extraction import api
from verified_extraction.extract import candidates_for, extract_fields, snapshot_hash, verify_candidate
from verified_extraction.fetch import HTTPFetcher, Page, decode_html
from verified_extraction.models import JobRequest
from verified_extraction.service import run_job
from verified_extraction.store import Store

SCHEMA = {"type": "object", "properties": {"plan_price": {"type": "number"},
    "usage_limit": {"type": "integer"}, "support": {"type": "string"}}}


def test_charset_policy_and_original_byte_hash(monkeypatch):
    raw = '<meta charset="windows-1252"><p>Support: Café</p>'.encode("cp1252")
    fetcher = HTTPFetcher({"example.com"}, time.monotonic() + 10)
    monkeypatch.setattr(fetcher, "_request", lambda url: (404, {}, b"") if url.endswith("robots.txt") else
        (200, {"content-type": "text/html"}, raw))
    page = fetcher.fetch("https://example.com/")
    assert page.html.endswith("Café</p>")
    assert page.encoding == "cp1252" and page.decoding_errors == 0
    assert snapshot_hash(page) == hashlib.sha256(raw).hexdigest()
    assert snapshot_hash(page) != snapshot_hash(page.html)
    text, encoding, errors = decode_html(b'<meta charset="windows-1252"><p>Caf\xc3\xa9</p>',
                                          "text/html; charset=utf-8")
    assert encoding == "utf-8" and errors == 0 and "Café" in text
    text, encoding, errors = decode_html(b"<p>\xff</p>", "text/html; charset=utf-8")
    assert errors == 1 and "\ufffd" in text


def test_reproducible_locator_and_value_verification():
    page = Page("https://example.com/", "<main><p>Support: Email</p><p>Support: Phone</p></main>", "now", [])
    candidate = candidates_for(page, "support", SCHEMA["properties"]["support"])[0]
    assert candidate.evidence.locator == "main:nth-of-type(1) > p:nth-of-type(1)"
    assert verify_candidate(candidate, page)
    assert not verify_candidate(candidate.model_copy(update={"value": "Phone"}), page)
    evidence = candidate.evidence.model_copy(update={"locator": "main:nth-of-type(1) > p:nth-of-type(2)"})
    assert not verify_candidate(candidate.model_copy(update={"evidence": evidence}), page)
    evidence = candidate.evidence.model_copy(update={"label": "Plan price"})
    assert not verify_candidate(candidate.model_copy(update={"evidence": evidence}), page)


def test_price_periods_conflict_and_schema_unit_filters():
    page = Page("https://example.com/", "<p>Plan price: $29/month</p><p>Plan price: $29/year</p>", "now", [])
    fields = extract_fields([page], SCHEMA["properties"], False)
    assert fields["plan_price"].state == "conflicting"
    assert {(c.value, c.unit, c.currency) for c in fields["plan_price"].candidates} == {
        (29.0, "month", "USD"), (29.0, "year", "USD")}
    monthly = {"plan_price": {"type": "number", "x-unit": "month", "x-currency": "USD"}}
    assert extract_fields([page], monthly, False)["plan_price"].value == 29
    yearly_only = Page(page.url, "<p>Plan price: $29/year</p>", "now", [])
    assert extract_fields([yearly_only], monthly, False)["plan_price"].state == "unverified"
    unsupported = Page(page.url, "<p>Plan price: $29/fortnight</p>", "now", [])
    assert extract_fields([unsupported], monthly, False)["plan_price"].state == "unverified"


def test_raw_capture_api_and_inert_review(monkeypatch):
    path = Path(f"test-{uuid.uuid4().hex}.db")
    monkeypatch.setattr(api, "store", Store(str(path)))
    monkeypatch.setenv("VE_KEYS", '{"a":"secret-a","b":"secret-b"}')
    raw = '<p>Support: Café</p><p>Plan price: $29/month</p><p>Usage limit: 10</p><script>alert(1)</script>'.encode("cp1252")
    decoded, encoding, errors = decode_html(raw, "text/html; charset=windows-1252")
    class Fetcher:
        def fetch(self, url):
            return Page(url, decoded, "now", [], raw, encoding, errors)
    monkeypatch.setattr(api, "run_job", lambda req: run_job(req, Fetcher()))
    body = {"url": "https://example.com/", "schema": SCHEMA, "idempotency_key": "one",
            "options": {"max_pages": 1, "max_depth": 0, "deadline_seconds": 10}}
    client = TestClient(api.app)
    auth = {"Authorization": "Bearer secret-a"}
    result = client.post("/v1/jobs", json=body, headers=auth).json()
    digest = result["pages"][0]["snapshot_hash"]
    assert client.get(f"/v1/jobs/{result['job_id']}/snapshots/{digest}/raw", headers=auth).content == raw
    assert client.get(f"/v1/jobs/{result['job_id']}/snapshots/{digest}/raw",
                      headers={"Authorization": "Bearer secret-b"}).status_code == 404
    review = client.get("/review")
    assert review.status_code == 200 and "textContent" in review.text
    assert "secret-a" not in review.text
    old_review = client.get(f"/v1/jobs/{result['job_id']}/review", headers=auth)
    assert "<script>alert(1)</script>" not in old_review.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in old_review.text
    path.unlink()

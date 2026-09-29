import json
import uuid
from pathlib import Path

import pytest

from benchmark.frozen import export_local, load_labeled_case
from benchmark.run import load_cases, score
from verified_extraction.extract import snapshot_hash, dom_path
from bs4 import BeautifulSoup
from verified_extraction.fetch import Page
from verified_extraction.models import JobRequest
from verified_extraction.service import run_job
from verified_extraction.store import Store


def test_frozen_bundle_requires_independent_source_labels():
    root = Path(f"test-bundle-{uuid.uuid4().hex}")
    root.mkdir()
    db_path = root / "jobs.db"
    store = Store(str(db_path))
    request = JobRequest.model_validate({"url": "https://frozen.example/", "schema": {"type": "object",
        "properties": {"support": {"type": "string"}, "plan_price": {"type": "number"},
                       "usage_limit": {"type": "integer"}}}, "idempotency_key": "one",
        "options": {"max_pages": 1, "max_depth": 0, "deadline_seconds": 10}})
    class Fetcher:
        def fetch(self, url):
            return Page(url, "<article><h3>Test Plan</h3><p>Support: Email</p></article>",
                        "2026-01-01T00:00:00+00:00", [])
    result, captures = run_job(request, Fetcher())
    store.put("tenant", result.job_id, "one", store.request_hash(request.model_dump(by_alias=True)),
              result.model_dump(), captures, request_payload=request.model_dump(by_alias=True))
    bundle = root / "site"
    try:
        manifest = export_local(store, "tenant", result.job_id, bundle, "frozen.example",
                                "development", "static", "Test Plan", "synthetic fixture authored locally")
        template = json.loads((bundle / "labels.template.json").read_text(encoding="utf-8"))
        assert "machine_result" not in template
        labels_path = bundle / "labels.json"
        labels_path.write_text(json.dumps(template), encoding="utf-8")
        with pytest.raises(ValueError, match="Blind labels"):
            load_labeled_case(bundle / "manifest.json", labels_path)
        template["reviewer_id"] = "independent-reviewer-1"
        for item in template["fields"].values():
            item["state"] = "missing"
            item["reviewer_time_seconds"] = 5
        evidence = result.fields["support"].evidence[0]
        soup = BeautifulSoup(next(iter(captures.values())).html, "html.parser")
        scope, plan_node, value_node = soup.select_one("article"), soup.select_one("h3"), soup.select_one("p")
        source = {"source_url": evidence.source_url, "snapshot_hash": evidence.snapshot_hash,
            "fetched_at": evidence.fetched_at, "scope_locator": dom_path(scope), "relation": "same_scope",
            "raw_value": "Email", "nodes": [
                {"role": "plan", "locator": dom_path(plan_node), "excerpt": "Test Plan"},
                {"role": "value", "locator": dom_path(value_node), "excerpt": "Support: Email"}]}
        template["fields"]["support"].update({"state": "verified", "value": "Email",
            "evidence": [source]})
        template["fields"]["support"]["value"] = "Phone"
        labels_path.write_text(json.dumps(template), encoding="utf-8")
        with pytest.raises(ValueError, match="unsupported"):
            load_labeled_case(bundle / "manifest.json", labels_path)
        template["fields"]["support"]["value"] = "Email"
        labels_path.write_text(json.dumps(template), encoding="utf-8")
        case = load_labeled_case(bundle / "manifest.json", labels_path)
        report = score([case])
        assert report["summary"]["denominators"]["correct_accepted"] == 1
        assert report["summary"]["review_minutes_per_correct_accepted_field"] is None
        assert case["blind_label_seconds"] == 15
        assisted = {"label_schema_version": "1.1", "job_id": result.job_id,
            "review_sessions": [{"started_at": 10, "stopped_at": 55, "elapsed_seconds": 45}]}
        (bundle / "assisted_reviews.json").write_text(json.dumps(assisted), encoding="utf-8")
        with_assisted = score([load_labeled_case(bundle / "manifest.json", labels_path)])
        assert with_assisted["summary"]["review_minutes_per_correct_accepted_field"] == 0.75
        assert report["per_case"][0]["raw_result"] is not None
        baseline = json.loads((bundle / "manual_baseline.template.json").read_text(encoding="utf-8"))
        baseline["analyst_id"] = "manual-analyst"
        baseline["elapsed_seconds"] = 90
        for item in baseline["fields"].values():
            item["state"] = "missing"
        baseline["fields"]["support"].update({"state": "verified", "value": "Phone",
            "evidence": [source]})
        (bundle / "manual_baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
        with_baseline = score([load_labeled_case(bundle / "manifest.json", labels_path)])
        assert with_baseline["summary"]["manual_baseline_minutes_per_site"] == 1.5
        assert with_baseline["summary"]["manual_baseline_errors"] == 1
        assert with_baseline["summary"]["paired_assisted_time_reduction"] == 0.5
        manifest["pages"][request.url]["snapshot_hash"] = "0" * 64
        (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(ValueError, match="hash mismatch"):
            load_labeled_case(bundle / "manifest.json", labels_path)
    finally:
        for file in (bundle / "captures").glob("*"):
            file.unlink()
        (bundle / "captures").rmdir()
        for file in bundle.glob("*"):
            file.unlink()
        bundle.rmdir()
        db_path.unlink()
        root.rmdir()


def test_held_out_requires_two_blind_labels_and_audited_adjudication():
    import base64
    import hashlib
    bundle = Path(f"test-labels-{uuid.uuid4().hex}")
    bundle.mkdir()
    try:
        raw = b"<p>Support: Email</p>"
        digest = hashlib.sha256(raw).hexdigest()
        manifest = {"fixture_version": "real-1.1", "site_id": "site-1", "split": "held_out",
            "category": "static", "seed": "https://site.example/", "schema": {"type": "object",
                "properties": {"support": {"type": "string"}}}, "options": {},
            "page_hints": [], "allowed_hostnames": [], "permission_note": "synthetic",
            "pages": {"https://site.example/": {"raw_base64": base64.b64encode(raw).decode(),
                "snapshot_hash": digest, "content_type": "text/html", "final_url": "https://site.example/"}}}
        (bundle / "manifest.json").write_text(json.dumps(manifest))
        field = {"state": "verified", "value": "Email", "unit": None, "currency": None,
            "source_url": "https://site.example/", "snapshot_hash": digest,
            "excerpt": "Support: Email", "raw_value": "Email", "judgment": "literal",
            "reviewer_time_seconds": 4}
        def label(reviewer, item):
            return {"label_schema_version": "1.1", "site_id": "site-1", "split": "held_out",
                "labeling_mode": "blind", "reviewer_id": reviewer, "fields": {"support": item}}
        first = label("a", field)
        second = label("b", {**field, "state": "missing", "value": None, "reviewer_time_seconds": 5})
        (bundle / "labels.reviewer-a.json").write_text(json.dumps(first))
        (bundle / "labels.reviewer-b.json").write_text(json.dumps(second))
        record = {"adjudication_schema_version": "1.0", "site_id": "site-1", "reviewer_id": "c",
            "fields": {"support": {"decision": "agree", "reason": "", "time_spent_seconds": 3}}}
        (bundle / "adjudication.json").write_text(json.dumps(record))
        with pytest.raises(ValueError, match="disagrees"):
            load_labeled_case(bundle / "manifest.json", bundle / "labels.json")
        record["fields"]["support"].update({"decision": "select_a", "reason": "visible literal claim"})
        (bundle / "adjudication.json").write_text(json.dumps(record))
        case = load_labeled_case(bundle / "manifest.json", bundle / "labels.json")
        assert case["expected"]["support"]["value"] == "Email"
        assert case["blind_label_seconds"] == 12
        assert case["review_seconds"] is None
        assert case["independent_labels"]["reviewer_b"]["fields"]["support"]["state"] == "missing"
    finally:
        for file in bundle.iterdir():
            file.unlink()
        bundle.rmdir()


def test_conflict_score_requires_labeled_source_binding():
    _, cases = load_cases("v2")
    case = next(item for item in cases if item["id"] == "hold-period-conflict")
    case = json.loads(json.dumps(case))
    field = case["expected"]["plan_price"]
    assert score([case])["per_case"][0]["fields"]["plan_price"]["correct"]
    field["candidates"][0]["source_url"] = "https://wrong.invalid/"
    report = score([case])
    assert report["per_case"][0]["fields"]["plan_price"]["evidence_valid"]
    assert not report["per_case"][0]["fields"]["plan_price"]["correct"]


def test_numeric_label_rejects_wrong_currency_or_period():
    import base64
    import hashlib
    bundle = Path(f"test-labels-{uuid.uuid4().hex}")
    bundle.mkdir()
    try:
        raw = b"<p>Price: $29/month</p>"
        digest = hashlib.sha256(raw).hexdigest()
        manifest = {"fixture_version": "real-1.1", "site_id": "price-site", "split": "development",
            "category": "pricing", "seed": "https://price.example/", "schema": {"type": "object",
                "properties": {"price": {"type": "number"}}}, "options": {},
            "page_hints": [], "allowed_hostnames": [], "permission_note": "synthetic",
            "pages": {"https://price.example/": {"raw_base64": base64.b64encode(raw).decode(),
                "snapshot_hash": digest, "content_type": "text/html", "final_url": "https://price.example/"}}}
        label = {"label_schema_version": "1.1", "site_id": "price-site", "split": "development",
            "labeling_mode": "blind", "reviewer_id": "independent", "fields": {"price": {
                "state": "verified", "value": 29, "unit": "month", "currency": "EUR",
                "source_url": "https://price.example/", "snapshot_hash": digest,
                "excerpt": "Price: $29/month", "raw_value": "$29/month", "judgment": "literal",
                "reviewer_time_seconds": 4}}}
        (bundle / "manifest.json").write_text(json.dumps(manifest))
        (bundle / "labels.json").write_text(json.dumps(label))
        with pytest.raises(ValueError, match="currency disagrees"):
            load_labeled_case(bundle / "manifest.json", bundle / "labels.json")
        label["fields"]["price"]["currency"] = "USD"
        label["fields"]["price"]["unit"] = "year"
        (bundle / "labels.json").write_text(json.dumps(label))
        with pytest.raises(ValueError, match="unit or currency disagrees"):
            load_labeled_case(bundle / "manifest.json", bundle / "labels.json")
    finally:
        for file in bundle.iterdir():
            file.unlink()
        bundle.rmdir()

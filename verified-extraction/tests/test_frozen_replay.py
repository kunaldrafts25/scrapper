import json
import uuid
from pathlib import Path

import pytest

from benchmark.frozen import export_local, load_labeled_case
from benchmark.run import load_cases, score
from verified_extraction.extract import snapshot_hash
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
            return Page(url, "<p>Support: Email</p>", "2026-01-01T00:00:00+00:00", [])
    result, captures = run_job(request, Fetcher())
    store.put("tenant", result.job_id, "one", store.request_hash(request.model_dump(by_alias=True)),
              result.model_dump(), captures, request_payload=request.model_dump(by_alias=True))
    bundle = root / "site"
    try:
        manifest = export_local(store, "tenant", result.job_id, bundle, "frozen.example",
                                "development", "static", "synthetic fixture authored locally")
        template = json.loads((bundle / "labels.template.json").read_text(encoding="utf-8"))
        assert "machine_result" not in template
        labels_path = bundle / "labels.json"
        labels_path.write_text(json.dumps(template), encoding="utf-8")
        with pytest.raises(ValueError, match="reviewer identity"):
            load_labeled_case(bundle / "manifest.json", labels_path)
        template["reviewer_id"] = "independent-reviewer-1"
        for item in template["fields"].values():
            item["state"] = "missing"
            item["reviewer_time_seconds"] = 5
        evidence = result.fields["support"].evidence[0]
        template["fields"]["support"].update({"state": "verified", "value": "Email",
            "source_url": evidence.source_url, "snapshot_hash": evidence.snapshot_hash,
            "excerpt": evidence.excerpt, "raw_value": "Email"})
        template["fields"]["support"]["value"] = "Phone"
        labels_path.write_text(json.dumps(template), encoding="utf-8")
        with pytest.raises(ValueError, match="disagrees"):
            load_labeled_case(bundle / "manifest.json", labels_path)
        template["fields"]["support"]["value"] = "Email"
        labels_path.write_text(json.dumps(template), encoding="utf-8")
        case = load_labeled_case(bundle / "manifest.json", labels_path)
        report = score([case])
        assert report["summary"]["denominators"]["correct_accepted"] == 1
        assert report["summary"]["review_minutes_per_correct_accepted_field"] == 0.25
        assert report["per_case"][0]["raw_result"] is not None
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
        assert case["review_seconds"] == 12
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

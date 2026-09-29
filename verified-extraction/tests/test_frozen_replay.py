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
                                "held_out", "static", "synthetic fixture authored locally")
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
            "excerpt": evidence.excerpt})
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

import base64
import hashlib
import json
import uuid
from pathlib import Path

from bs4 import BeautifulSoup

from benchmark.frozen import load_labeled_case
from benchmark.run import score
from verified_extraction.extract import dom_path


HTML = ("<section><article><h3>Team</h3><p>$20/month</p><p>20 per month</p></article>"
        "<article><h3>Pro</h3><p>$20/month</p></article></section>")
URL = "https://pricing.example/plans"
FETCHED = "2026-01-01T00:00:00+00:00"


def _source(article_index: int, paragraph_index: int, raw_value: str) -> dict:
    soup = BeautifulSoup(HTML, "html.parser")
    article = soup.select("article")[article_index]
    heading = article.select_one("h3")
    value = article.select("p")[paragraph_index]
    digest = hashlib.sha256(HTML.encode()).hexdigest()
    return {"source_url": URL, "snapshot_hash": digest, "fetched_at": FETCHED,
        "scope_locator": dom_path(article), "relation": "same_scope", "raw_value": raw_value,
        "nodes": [{"role": "plan", "locator": dom_path(heading), "excerpt": heading.get_text(" ", strip=True)},
                  {"role": "value", "locator": dom_path(value), "excerpt": value.get_text(" ", strip=True)}]}


def _bundle():
    folder = Path(f"test-ground-truth-{uuid.uuid4().hex}")
    folder.mkdir()
    raw = HTML.encode()
    digest = hashlib.sha256(raw).hexdigest()
    schema = {"type": "object", "properties": {"named_plan": {"type": "string", "title": "Plan"},
        "listed_price": {"type": "number", "title": "Price"},
        "support_channel": {"type": "string", "title": "Support"}}}
    manifest = {"fixture_version": "real-2.0", "site_id": "pricing-example", "split": "development",
        "category": "pricing", "seed": URL, "plan_name": "Team", "schema": schema,
        "options": {"max_pages": 1, "max_depth": 0, "deadline_seconds": 10},
        "page_hints": [], "allowed_hostnames": [], "permission_note": "synthetic",
        "pages": {URL: {"raw_base64": base64.b64encode(raw).decode(), "snapshot_hash": digest,
            "content_type": "text/html", "final_url": URL, "fetched_at": FETCHED}}}
    missing = {"state": "missing", "value": None, "plan_name": "Team", "judgment": "explicit",
        "evidence": [], "reviewer_time_seconds": 1}
    price = {"state": "verified", "value": 20, "plan_name": "Team", "unit": "month",
        "currency": None, "billing_period": "month", "judgment": "explicit",
        "evidence": [_source(0, 0, "$20/month")], "reviewer_time_seconds": 2}
    labels = {"label_schema_version": "2.0", "site_id": manifest["site_id"],
        "split": "development", "labeling_mode": "blind", "reviewer_id": "blind-a",
        "plan_name": "Team", "fields": {"named_plan": dict(missing), "listed_price": price,
                                       "support_channel": dict(missing)}}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    return folder, manifest, labels


def _baseline(source: dict) -> dict:
    return {"baseline_schema_version": "2.0", "site_id": "pricing-example",
        "analyst_id": "manual-analyst", "elapsed_seconds": 60,
        "fields": {"named_plan": {"state": "missing", "value": None, "plan_name": "Team"},
                   "listed_price": {"state": "verified", "value": 20, "plan_name": "Team",
                       "unit": "month", "currency": None, "billing_period": "month",
                       "judgment": "explicit", "evidence": [source]},
                   "support_channel": {"state": "missing", "value": None, "plan_name": "Team"}}}


def _cleanup(folder: Path):
    for path in folder.iterdir():
        path.unlink()
    folder.rmdir()


def test_pricing_card_is_ground_truth_even_when_extractor_misses_it():
    folder, _, _ = _bundle()
    try:
        case = load_labeled_case(folder / "manifest.json", folder / "labels.json")
        assert case["expected"]["listed_price"]["value"] == 20
        report = score([case])
        assert report["per_case"][0]["fields"]["listed_price"]["actual_state"] == "missing"
        assert report["summary"]["denominators"]["expected_values"] == 1
        assert report["summary"]["denominators"]["missed_values"] == 1
        assert report["summary"]["field_recall"] == 0
    finally:
        _cleanup(folder)


def test_manual_baseline_accepts_different_valid_excerpt_and_rejects_bad_evidence():
    folder, _, _ = _bundle()
    try:
        baseline = _baseline(_source(0, 1, "20 per month"))
        path = folder / "manual_baseline.json"
        path.write_text(json.dumps(baseline), encoding="utf-8")
        case = load_labeled_case(folder / "manifest.json", folder / "labels.json")
        assert case["manual_baseline_errors"] == 0
        baseline["fields"]["listed_price"]["evidence"][0]["snapshot_hash"] = "0" * 64
        path.write_text(json.dumps(baseline), encoding="utf-8")
        case = load_labeled_case(folder / "manifest.json", folder / "labels.json")
        assert case["manual_baseline_errors"] == 1
        assert "invalid cited evidence" in case["manual_baseline_error_details"]["listed_price"]
    finally:
        _cleanup(folder)


def test_manual_baseline_rejects_same_price_from_wrong_plan():
    folder, _, _ = _bundle()
    try:
        baseline = _baseline(_source(1, 0, "$20/month"))
        (folder / "manual_baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
        case = load_labeled_case(folder / "manifest.json", folder / "labels.json")
        assert case["manual_baseline_errors"] == 1
        assert "different plan" in case["manual_baseline_error_details"]["listed_price"]
    finally:
        _cleanup(folder)


def test_hidden_only_value_cannot_support_independent_label():
    folder, manifest, labels = _bundle()
    try:
        html = "<article><h3>Team</h3><p>Support: Email <span hidden>Phone</span></p></article>"
        raw = html.encode()
        digest = hashlib.sha256(raw).hexdigest()
        manifest["pages"][URL]["raw_base64"] = base64.b64encode(raw).decode()
        manifest["pages"][URL]["snapshot_hash"] = digest
        soup = BeautifulSoup(html, "html.parser")
        article = soup.article
        labels["fields"]["listed_price"] = {"state": "missing", "value": None,
            "plan_name": "Team", "evidence": [], "reviewer_time_seconds": 1}
        labels["fields"]["support_channel"] = {"state": "verified", "value": "Phone",
            "plan_name": "Team", "judgment": "explicit", "reviewer_time_seconds": 1,
            "evidence": [{"source_url": URL, "snapshot_hash": digest, "fetched_at": FETCHED,
                "scope_locator": dom_path(article), "relation": "same_scope", "raw_value": "Phone",
                "nodes": [{"role": "plan", "locator": dom_path(soup.h3), "excerpt": "Team"},
                          {"role": "value", "locator": dom_path(soup.p),
                           "excerpt": "Support: Email"}]}]}
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (folder / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
        import pytest
        with pytest.raises(ValueError, match="raw value is absent"):
            load_labeled_case(folder / "manifest.json", folder / "labels.json")
    finally:
        _cleanup(folder)


def test_corrected_final_row_scores_source_backed_fields_separately():
    folder, manifest, _ = _bundle()
    try:
        manifest["source_job_id"] = "job-1"
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        assisted = {"label_schema_version": "1.1", "job_id": "job-1",
            "reviews": {"listed_price": {"verdict": "wrong"}},
            "review_sessions": [{"field_name": "listed_price", "reviewer_id": "assisted-a",
                                 "started_at": 1, "stopped_at": 11, "elapsed_seconds": 10}]}
        (folder / "assisted_reviews.json").write_text(json.dumps(assisted), encoding="utf-8")
        corrected = {"corrected_row_schema_version": "1.0", "site_id": "pricing-example",
            "source_job_id": "job-1", "reviewer_id": "assisted-a",
            "fields": _baseline(_source(0, 1, "20 per month"))["fields"]}
        path = folder / "corrected_row.json"
        path.write_text(json.dumps(corrected), encoding="utf-8")
        case = load_labeled_case(folder / "manifest.json", folder / "labels.json")
        assert case["corrected_row_errors"] == 0
        metrics = score([case])["summary"]
        assert metrics["customer_task_recall"] == 0
        assert metrics["corrected_row_error_rate"] == 0
        assert metrics["reviewer_verdict_error_rate"] == 0
        corrected["fields"]["listed_price"]["evidence"] = [_source(1, 0, "$20/month")]
        path.write_text(json.dumps(corrected), encoding="utf-8")
        case = load_labeled_case(folder / "manifest.json", folder / "labels.json")
        assert case["corrected_row_errors"] == 1
        assert score([case])["summary"]["corrected_row_error_rate"] == .3333
    finally:
        _cleanup(folder)


def test_held_out_keeps_two_blind_labels_and_adjudication():
    folder, manifest, first = _bundle()
    try:
        manifest["split"] = "held_out"
        first["split"] = "held_out"
        second = json.loads(json.dumps(first))
        second["reviewer_id"] = "blind-b"
        second["fields"]["listed_price"] = {"state": "missing", "value": None,
            "plan_name": "Team", "judgment": "explicit", "evidence": [], "reviewer_time_seconds": 3}
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (folder / "labels.reviewer-a.json").write_text(json.dumps(first), encoding="utf-8")
        (folder / "labels.reviewer-b.json").write_text(json.dumps(second), encoding="utf-8")
        adjudication = {"adjudication_schema_version": "1.0", "site_id": manifest["site_id"],
            "reviewer_id": "adjudicator", "fields": {
                name: {"decision": "select_a" if name == "listed_price" else "agree",
                       "reason": "Team card shows $20/month" if name == "listed_price" else None,
                       "time_spent_seconds": 2}
                for name in first["fields"]}}
        (folder / "adjudication.json").write_text(json.dumps(adjudication), encoding="utf-8")
        case = load_labeled_case(folder / "manifest.json", folder / "labels.json")
        assert case["expected"]["listed_price"]["value"] == 20
        assert case["independent_labels"]["reviewer_b"]["fields"]["listed_price"]["state"] == "missing"
        assert case["independent_labels"]["adjudication"]["fields"]["listed_price"]["reason"]
    finally:
        _cleanup(folder)

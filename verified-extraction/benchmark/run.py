"""Frozen, synthetic benchmark. No adapter here performs a network call."""
from __future__ import annotations

import argparse
import base64
import json
import random
import time
from collections import defaultdict
from pathlib import Path
from typing import Protocol

from verified_extraction.extract import verify_candidate
from verified_extraction.fetch import Page, decode_html
from verified_extraction.models import Candidate, Evidence, JobRequest
from verified_extraction.security import FetchError
from verified_extraction.service import run_job
from .ground_truth import machine_evidence_in_scope

SCHEMA = {"type": "object", "properties": {"plan_price": {"type": "number"},
    "usage_limit": {"type": "integer"}, "support": {"type": "string"}}}


class BenchmarkAdapter(Protocol):
    def run(self, case: dict) -> tuple[dict, dict[str, Page]]: ...


class LocalAdapter:
    def run(self, case: dict) -> tuple[dict, dict[str, Page]]:
        class FrozenFetcher:
            def fetch(self, url):
                fixture = case["pages"][url]
                if "error" in fixture:
                    raise FetchError(fixture["error"], "Frozen access-policy fixture")
                encoding = fixture.get("encoding", "utf-8")
                raw = base64.b64decode(fixture["raw_base64"], validate=True) if "raw_base64" in fixture else fixture["html"].encode(encoding)
                decoded, chosen, errors = decode_html(raw, fixture.get("content_type", f"text/html; charset={encoding}"))
                return Page(fixture.get("final_url", url), decoded, fixture.get("fetched_at", "2026-01-01T00:00:00+00:00"),
                            fixture.get("redirects", []), raw, chosen, errors, fixture.get("content_type", f"text/html; charset={encoding}"))
        seed = case.get("seed", "https://" + case["site"] + "/")
        request = JobRequest.model_validate({"url": seed, "schema": case.get("schema", SCHEMA),
            "idempotency_key": case["id"], "page_hints": case.get("page_hints", []),
            "allowed_hostnames": case.get("allowed_hostnames", []),
            "options": case.get("options", {"max_pages": 3, "max_depth": 1, "deadline_seconds": 10})})
        result, captures = run_job(request, FrozenFetcher())
        return result.model_dump(), captures


class ExternalAdapter:
    """Unconfigured Ryze, Firecrawl, Zyte, Diffbot, and crawler-plus-model contracts."""
    def __init__(self, name: str):
        self.name = name

    def run(self, case: dict):
        raise RuntimeError(f"{self.name} is unconfigured; no result is claimed")


EXTERNAL = {name: ExternalAdapter(name) for name in ("ryze", "firecrawl", "zyte", "diffbot", "crawler_plus_model")}


def load_cases(suite: str) -> tuple[str, list[dict]]:
    root = Path(__file__).parent
    if suite == "smoke":
        old = json.loads((root / "cases.json").read_text(encoding="utf-8"))
        cases = []
        for item in old:
            site = f"smoke-{item['id']}.invalid"
            pages = {url.replace("bench.example", site): {"html": html} for url, html in item["pages"].items()}
            expected = {key: {"state": "verified", "value": value} if value is not None else {"state": "missing"}
                        for key, value in item["expected"].items()}
            for field in item.get("conflicting", []):
                expected[field] = {"state": "conflicting"}
            cases.append({"id": item["id"], "site": site, "split": item["split"],
                          "category": "smoke", "pages": pages, "expected": expected})
        return "smoke-1", cases
    data = json.loads((root / "v2_cases.json").read_text(encoding="utf-8"))
    cases = data["cases"]
    sites = defaultdict(set)
    for case in cases:
        sites[case["site"]].add(case["split"])
        assert set(case["expected"]) == set(SCHEMA["properties"])
    assert all(len(splits) == 1 for splits in sites.values()), "A site spans development and held-out splits"
    return data["fixture_version"], cases


def evidence_checks(field: dict, kind: str, captures: dict[str, Page], page_rows: list[dict]) -> list[bool]:
    candidates = field["candidates"] if field["state"] == "conflicting" else [
        {"value": field["value"], "unit": field.get("unit"), "currency": field.get("currency"),
         "numeric_encoding": field.get("numeric_encoding"),
         "evidence": ev} for ev in field["evidence"]] if field["state"] == "verified" else []
    checks = []
    for item in candidates:
        ev = Evidence.model_validate(item["evidence"])
        capture = captures.get(ev.snapshot_hash)
        bound = next((row for row in page_rows if row.get("url") == ev.source_url and
                      row.get("fetched_at") == ev.fetched_at and row.get("snapshot_hash") == ev.snapshot_hash), None)
        source = (Page(ev.source_url, capture.raw.decode(bound.get("encoding", "utf-8"), "replace"),
                       ev.fetched_at, [], capture.raw, bound.get("encoding", "utf-8"),
                       bound.get("decoding_errors", 0), bound.get("content_type", capture.content_type))
                  if capture and bound else None)
        candidate = Candidate(value=item["value"], unit=item.get("unit"), currency=item.get("currency"),
                              value_type=kind, numeric_encoding=item.get("numeric_encoding"), evidence=ev)
        checks.append(source is not None and verify_candidate(candidate, source))
    return checks


def score(cases: list[dict]) -> dict:
    rows = []
    for case in cases:
        start = time.monotonic()
        try:
            result, captures = LocalAdapter().run(case)
            failure = None
        except Exception as exc:
            result, captures, failure = None, {}, type(exc).__name__
        duration = round(time.monotonic() - start, 6)
        fields = {}
        if result:
            for name, expected in case["expected"].items():
                actual = result["fields"][name]
                checks = evidence_checks(actual, case.get("schema", SCHEMA)["properties"][name]["type"], captures, result["pages"])
                accepted = actual["state"] == "verified"
                evidence_valid = bool(checks) and all(checks) if actual["state"] in {"verified", "conflicting"} else None
                expected_value = expected.get("value")
                correct = actual["state"] == expected["state"]
                if accepted:
                    correct = (correct and actual["value"] == expected_value and bool(evidence_valid))
                    if "unit" in expected:
                        correct = correct and actual.get("unit") == expected["unit"]
                    if "currency" in expected:
                        correct = correct and actual.get("currency") == expected["currency"]
                    if name == "listed_price" and expected.get("billing_period"):
                        correct = correct and actual.get("unit") == expected["billing_period"]
                    if "evidence" in expected:
                        correct = correct and machine_evidence_in_scope(actual["evidence"], expected)
                    elif "excerpt" in expected:
                        correct = correct and any(ev["excerpt"] == expected["excerpt"] and
                            ev["source_url"] == expected.get("source_url", case.get("seed", "https://" + case["site"] + "/")) and
                            ("snapshot_hash" not in expected or ev["snapshot_hash"] == expected["snapshot_hash"])
                            for ev in actual["evidence"])
                if actual["state"] == "conflicting" and "candidates" in expected:
                    if expected["candidates"] and "evidence" in expected["candidates"][0]:
                        remaining = list(actual["candidates"])
                        correct = bool(evidence_valid)
                        for claim in expected["candidates"]:
                            match = next((item for item in remaining if
                                item["value"] == claim["value"] and
                                item.get("unit") == claim.get("unit") and
                                item.get("currency") == claim.get("currency") and
                                machine_evidence_in_scope([item["evidence"]], claim)), None)
                            if match is None:
                                correct = False
                                break
                            remaining.remove(match)
                        correct = correct and not remaining
                    else:
                        remaining = list(actual["candidates"])
                        for claim in expected["candidates"]:
                            match = next((item for item in remaining if
                                item["value"] == claim["value"] and
                                item.get("unit") == claim.get("unit") and
                                item.get("currency") == claim.get("currency") and
                                item["evidence"]["excerpt"] == claim["excerpt"] and
                                ("source_url" not in claim or item["evidence"]["source_url"] == claim["source_url"]) and
                                ("snapshot_hash" not in claim or item["evidence"]["snapshot_hash"] == claim["snapshot_hash"])), None)
                            if match is None:
                                correct = False
                                break
                            remaining.remove(match)
                        else:
                            correct = bool(evidence_valid) and not remaining and len(actual["candidates"]) == len(expected["candidates"])
                fields[name] = {"expected": expected, "actual_state": actual["state"], "actual_value": actual["value"],
                    "actual_unit": actual.get("unit"), "actual_currency": actual.get("currency"),
                    "evidence_valid": evidence_valid, "correct": bool(correct)}
        else:
            fields = {name: {"expected": expected, "actual_state": "failed", "actual_value": None,
                "actual_unit": None, "actual_currency": None, "evidence_valid": None, "correct": False}
                for name, expected in case["expected"].items()}
        reviewed_row_correct = bool(case.get("plan_name")) and all(
            item["expected"]["state"] == "verified" and item["actual_state"] == "verified" and item["correct"]
            for item in fields.values())
        verdicts = (case.get("assisted_reviews") or {}).get("reviews", {})
        verdict_fields = sum(name in fields for name in verdicts)
        verdict_errors = sum((review.get("verdict") == "correct") != bool(fields[name]["correct"])
                             for name, review in verdicts.items() if name in fields)
        rows.append({"id": case["id"], "site": case["site"], "split": case["split"],
                     "category": case["category"], "latency_seconds": duration, "failure": failure,
                     "access_failure_codes": [item["code"] for item in result["errors"] if item["code"] in
                         {"ACCESS_DENIED", "ROBOTS_DENIED", "ROBOTS_UNAVAILABLE", "PRIVATE_TARGET", "OUT_OF_SCOPE"}]
                         if result else [],
                     "access_policy_violations": case.get("access_policy_violations", []),
                     "fields": fields, "result_status": result["status"] if result else None,
                     "reviewed_row_correct": reviewed_row_correct,
                     "review_seconds": case.get("review_seconds"),
                     "blind_label_seconds": case.get("blind_label_seconds"),
                     "live_elapsed_seconds": case.get("live_elapsed_seconds"),
                     "http_requests_started": case.get("http_requests_started"),
                     "preflight_http_requests_started": case.get("preflight_http_requests_started"),
                     "manual_baseline_seconds": case.get("manual_baseline_seconds"),
                     "manual_baseline_errors": case.get("manual_baseline_errors"),
                     "manual_baseline_fields": case.get("manual_baseline_fields"),
                     "corrected_row_errors": case.get("corrected_row_errors"),
                     "corrected_row_fields": case.get("corrected_row_fields"),
                     "corrected_row_error_details": case.get("corrected_row_error_details"),
                     "reviewer_verdict_errors": verdict_errors if verdict_fields else None,
                     "reviewer_verdict_fields": verdict_fields,
                     "plan_name": case.get("plan_name"), "code_revision": case.get("code_revision"),
                     "independent_labels": case.get("independent_labels"),
                     "manual_baseline": case.get("manual_baseline"),
                     "corrected_row": case.get("corrected_row"),
                     "assisted_reviews": case.get("assisted_reviews"),
                     "estimated_internal_cost_usd": result["usage"].get("estimated_internal_cost_usd") if result else None,
                     "raw_result": result})
    wrong_accepted = [{"site": row["site"], "field": name,
                       "expected": field["expected"], "actual_value": field["actual_value"],
                       "actual_unit": field["actual_unit"],
                       "actual_currency": field["actual_currency"],
                       "machine_evidence": row["raw_result"]["fields"][name]["evidence"]}
                      for row in rows for name, field in row["fields"].items()
                      if field["actual_state"] == "verified" and not field["correct"] and row["raw_result"]]
    return {"per_case": rows, "summary": summarize(rows), "wrong_accepted_examples": wrong_accepted,
            "by_category": {category: summarize([row for row in rows if row["category"] == category])
                            for category in sorted({row["category"] for row in rows})},
            "site_bootstrap_95pct": bootstrap_interval(rows),
            "failure_examples": [{"site": row["site"], "failure": row["failure"],
                                  "wrong_fields": [name for name, field in row["fields"].items() if not field["correct"]]}
                                 for row in rows if row["failure"] or any(not field["correct"] for field in row["fields"].values())]}


def bootstrap_interval(rows: list[dict]) -> dict:
    if len(rows) < 2:
        return {"field_precision": None, "field_recall": None}
    rng = random.Random(13)
    values = {"field_precision": [], "field_recall": []}
    for _ in range(500):
        sample = [rng.choice(rows) for _ in rows]
        summary = summarize(sample)
        for key in values:
            if summary[key] is not None:
                values[key].append(summary[key])
    output = {}
    for key, samples in values.items():
        samples.sort()
        output[key] = [samples[int(0.025 * (len(samples) - 1))], samples[int(0.975 * (len(samples) - 1))]] if samples else None
    return output


def summarize(rows: list[dict]) -> dict:
    counts = {key: 0 for key in ("fields", "expected_values", "accepted", "correct_accepted", "incorrect_accepted",
                                 "missed_values", "abstentions", "expected_conflicts", "detected_conflicts",
                                 "evidence_checked", "evidence_valid", "failures", "access_failures",
                                 "reviewed_rows_correct")}
    elapsed = 0.0
    review_seconds = 0.0
    reviewed_cases = 0
    baseline_cases = baseline_fields = baseline_errors = 0
    corrected_cases = corrected_fields = corrected_errors = 0
    verdict_fields = verdict_errors = 0
    baseline_seconds = live_seconds = 0.0
    live_cases = 0
    http_requests = 0
    preflight_http_requests = 0
    paired_cases = 0
    paired_baseline_seconds = paired_assisted_seconds = 0.0
    for row in rows:
        elapsed += row["latency_seconds"]
        if row.get("review_seconds") is not None:
            review_seconds += row["review_seconds"]
            reviewed_cases += 1
        if row.get("manual_baseline_seconds") is not None:
            baseline_cases += 1
            baseline_seconds += row["manual_baseline_seconds"]
            baseline_fields += row["manual_baseline_fields"]
            baseline_errors += row["manual_baseline_errors"]
        if row.get("corrected_row_errors") is not None:
            corrected_cases += 1
            corrected_fields += row["corrected_row_fields"]
            corrected_errors += row["corrected_row_errors"]
        verdict_fields += row.get("reviewer_verdict_fields", 0)
        verdict_errors += row.get("reviewer_verdict_errors") or 0
        if row.get("live_elapsed_seconds") is not None:
            live_cases += 1
            live_seconds += row["live_elapsed_seconds"]
        if row.get("http_requests_started") is not None:
            http_requests += row["http_requests_started"]
        if row.get("preflight_http_requests_started") is not None:
            preflight_http_requests += row["preflight_http_requests_started"]
        if row.get("manual_baseline_seconds") is not None and row.get("review_seconds") is not None:
            paired_cases += 1
            paired_baseline_seconds += row["manual_baseline_seconds"]
            paired_assisted_seconds += row["review_seconds"]
        counts["failures"] += row["failure"] is not None
        counts["access_failures"] += bool(row.get("access_failure_codes"))
        counts["reviewed_rows_correct"] += bool(row.get("reviewed_row_correct"))
        for field in row["fields"].values():
            counts["fields"] += 1
            expected_value = field["expected"]["state"] == "verified"
            counts["expected_values"] += expected_value
            accepted = field["actual_state"] == "verified"
            counts["accepted"] += accepted
            counts["correct_accepted"] += accepted and field["correct"]
            counts["incorrect_accepted"] += accepted and not field["correct"]
            counts["missed_values"] += expected_value and not (accepted and field["correct"])
            counts["abstentions"] += not accepted
            expected_conflict = field["expected"]["state"] == "conflicting"
            counts["expected_conflicts"] += expected_conflict
            counts["detected_conflicts"] += expected_conflict and field["correct"]
            if field["evidence_valid"] is not None:
                counts["evidence_checked"] += 1
                counts["evidence_valid"] += field["evidence_valid"]
    def ratio(n, d):
        return round(n / d, 4) if d else None
    return {"cases": len(rows), "denominators": counts,
        "field_precision": ratio(counts["correct_accepted"], counts["accepted"]),
        "field_recall": ratio(counts["correct_accepted"], counts["expected_values"]),
        "accepted_claim_correctness": ratio(counts["correct_accepted"], counts["accepted"]),
        "customer_task_recall": ratio(counts["correct_accepted"], counts["expected_values"]),
        "abstention_rate": ratio(counts["abstentions"], counts["fields"]),
        "conflict_detection": ratio(counts["detected_conflicts"], counts["expected_conflicts"]),
        "evidence_validity": ratio(counts["evidence_valid"], counts["evidence_checked"]),
        "mean_latency_seconds": ratio(elapsed, len(rows)),
        "failure_rate": ratio(counts["failures"], len(rows)),
        "access_failure_rate": ratio(counts["access_failures"], len(rows)),
        "reviewed_cases": reviewed_cases,
        "review_minutes_per_correct_accepted_field": ratio(review_seconds / 60, counts["correct_accepted"]) if reviewed_cases else None,
        "manual_baseline_cases": baseline_cases,
        "manual_baseline_minutes_per_site": ratio(baseline_seconds / 60, baseline_cases),
        "manual_baseline_error_rate": ratio(baseline_errors, baseline_fields),
        "manual_baseline_errors": baseline_errors, "manual_baseline_fields": baseline_fields,
        "corrected_row_cases": corrected_cases,
        "corrected_row_errors": corrected_errors, "corrected_row_fields": corrected_fields,
        "corrected_row_error_rate": ratio(corrected_errors, corrected_fields),
        "reviewer_verdict_errors": verdict_errors,
        "reviewer_verdict_fields": verdict_fields,
        "reviewer_verdict_error_rate": ratio(verdict_errors, verdict_fields),
        "paired_review_cases": paired_cases,
        "paired_assisted_time_reduction": ratio(paired_baseline_seconds - paired_assisted_seconds,
                                                 paired_baseline_seconds),
        "mean_live_latency_seconds": ratio(live_seconds, live_cases),
        "live_latency_cases": live_cases, "http_requests_started": http_requests if live_cases else None,
        "preflight_http_requests_started": preflight_http_requests if live_cases else None,
        "total_http_requests_started": http_requests + preflight_http_requests if live_cases else None,
        "setup_review_time_minutes": None, "cost_per_accepted_field_usd": None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=["v2", "smoke"], default="v2")
    parser.add_argument("--split", choices=["development", "held_out", "all"], default="all")
    args = parser.parse_args()
    version, all_cases = load_cases(args.suite)
    cases = [case for case in all_cases if args.split == "all" or case["split"] == args.split]
    print(json.dumps({"fixture_version": version, "suite": args.suite, "split": args.split,
                      "no_live_requests": True, **score(cases)}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()

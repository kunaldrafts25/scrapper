"""Frozen, synthetic benchmark. No adapter here performs a network call."""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Protocol

from verified_extraction.extract import verify_candidate
from verified_extraction.fetch import Page, decode_html
from verified_extraction.models import Candidate, Evidence, JobRequest
from verified_extraction.security import FetchError
from verified_extraction.service import run_job

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
                raw = fixture["html"].encode(encoding)
                decoded, chosen, errors = decode_html(raw, fixture.get("content_type", f"text/html; charset={encoding}"))
                return Page(fixture.get("final_url", url), decoded, "2026-01-01T00:00:00+00:00",
                            fixture.get("redirects", []), raw, chosen, errors)
        seed = "https://" + case["site"] + "/"
        request = JobRequest.model_validate({"url": seed, "schema": SCHEMA, "idempotency_key": case["id"],
            "options": {"max_pages": 3, "max_depth": 1, "deadline_seconds": 10}})
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


def evidence_checks(field: dict, kind: str, captures: dict[str, Page]) -> list[bool]:
    candidates = field["candidates"] if field["state"] == "conflicting" else [
        {"value": field["value"], "unit": field.get("unit"), "currency": field.get("currency"),
         "evidence": ev} for ev in field["evidence"]] if field["state"] == "verified" else []
    checks = []
    for item in candidates:
        ev = Evidence.model_validate(item["evidence"])
        source = captures.get(ev.snapshot_hash)
        candidate = Candidate(value=item["value"], unit=item.get("unit"), currency=item.get("currency"),
                              value_type=kind, evidence=ev)
        checks.append(source is not None and source.url == ev.source_url and verify_candidate(candidate, source))
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
                checks = evidence_checks(actual, SCHEMA["properties"][name]["type"], captures)
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
                    if "excerpt" in expected:
                        correct = correct and any(ev["excerpt"] == expected["excerpt"] and
                            ev["source_url"] == expected.get("source_url", "https://" + case["site"] + "/")
                            for ev in actual["evidence"])
                if actual["state"] == "conflicting" and "candidates" in expected:
                    expected_claims = {(c["value"], c.get("unit"), c.get("currency"), c["excerpt"]) for c in expected["candidates"]}
                    actual_claims = {(c["value"], c.get("unit"), c.get("currency"), c["evidence"]["excerpt"]) for c in actual["candidates"]}
                    correct = bool(evidence_valid) and actual_claims == expected_claims
                fields[name] = {"expected": expected, "actual_state": actual["state"], "actual_value": actual["value"],
                    "actual_unit": actual.get("unit"), "actual_currency": actual.get("currency"),
                    "evidence_valid": evidence_valid, "correct": bool(correct)}
        else:
            fields = {name: {"expected": expected, "actual_state": "failed", "actual_value": None,
                "actual_unit": None, "actual_currency": None, "evidence_valid": None, "correct": False}
                for name, expected in case["expected"].items()}
        rows.append({"id": case["id"], "site": case["site"], "split": case["split"],
                     "category": case["category"], "latency_seconds": duration, "failure": failure,
                     "fields": fields, "result_status": result["status"] if result else None})
    return {"per_case": rows, "summary": summarize(rows),
            "by_category": {category: summarize([row for row in rows if row["category"] == category])
                            for category in sorted({row["category"] for row in rows})}}


def summarize(rows: list[dict]) -> dict:
    counts = {key: 0 for key in ("fields", "expected_values", "accepted", "correct_accepted", "incorrect_accepted",
                                 "missed_values", "abstentions", "expected_conflicts", "detected_conflicts",
                                 "evidence_checked", "evidence_valid", "failures")}
    elapsed = 0.0
    for row in rows:
        elapsed += row["latency_seconds"]
        counts["failures"] += row["failure"] is not None
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
        "abstention_rate": ratio(counts["abstentions"], counts["fields"]),
        "conflict_detection": ratio(counts["detected_conflicts"], counts["expected_conflicts"]),
        "evidence_validity": ratio(counts["evidence_valid"], counts["evidence_checked"]),
        "mean_latency_seconds": ratio(elapsed, len(rows)),
        "failure_rate": ratio(counts["failures"], len(rows)),
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

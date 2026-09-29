from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Protocol

from verified_extraction.fetch import Page
from verified_extraction.models import JobRequest
from verified_extraction.service import run_job

SCHEMA = {"type": "object", "properties": {"plan_price": {"type": "number"},
    "usage_limit": {"type": "integer"}, "support": {"type": "string"}}}


class BenchmarkAdapter(Protocol):
    def run(self, case: dict) -> dict: ...


class LocalAdapter:
    def run(self, case: dict) -> dict:
        class FrozenFetcher:
            def fetch(self, url):
                if url not in case["pages"]:
                    raise KeyError(url)
                return Page(url, case["pages"][url], "2026-01-01T00:00:00+00:00", [])
        request = JobRequest.model_validate({"url": "https://bench.example/", "schema": SCHEMA,
            "idempotency_key": case["id"], "options": {"max_pages": 3, "max_depth": 1, "deadline_seconds": 10}})
        result, _ = run_job(request, FrozenFetcher())
        return result.model_dump()


class ExternalAdapter:
    """Contract for Ryze, Firecrawl, Zyte, Diffbot, and crawler-plus-model adapters.

    Live implementations require explicit approval and credentials; they are not run here.
    """
    def __init__(self, name: str):
        self.name = name

    def run(self, case: dict) -> dict:
        raise RuntimeError(f"{self.name} is unconfigured; no result is claimed")


EXTERNAL = {name: ExternalAdapter(name) for name in ("ryze", "firecrawl", "zyte", "diffbot", "crawler_plus_model")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["development", "held_out", "all"], default="all")
    args = parser.parse_args()
    cases = json.loads((Path(__file__).parent / "cases.json").read_text(encoding="utf-8"))
    cases = [case for case in cases if args.split == "all" or case["split"] == args.split]
    tp = fp = fn = abstain = conflict_ok = conflicts = failed = 0
    elapsed = []
    accepted = 0
    for case in cases:
        started = time.monotonic()
        try:
            result = LocalAdapter().run(case)
        except Exception:
            failed += 1
            continue
        elapsed.append(time.monotonic() - started)
        for name, expected in case["expected"].items():
            field = result["fields"][name]
            if field["state"] == "verified":
                accepted += 1
                if field["value"] == expected and expected is not None:
                    tp += 1
                else:
                    fp += 1
            else:
                abstain += 1
                if expected is not None:
                    fn += 1
            if name in case.get("conflicting", []):
                conflicts += 1
                conflict_ok += field["state"] == "conflicting"
    count = len(cases) * len(SCHEMA["properties"])
    print(json.dumps({"cases": len(cases), "split": args.split,
        "field_precision": tp / (tp + fp) if tp + fp else None,
        "field_recall": tp / (tp + fn) if tp + fn else None,
        "verified_field_precision": tp / accepted if accepted else None,
        "abstention_rate": abstain / count if count else None,
        "conflict_detection": conflict_ok / conflicts if conflicts else None,
        "mean_latency_seconds": sum(elapsed) / len(elapsed) if elapsed else None,
        "failure_rate": failed / len(cases) if cases else None,
        "setup_review_time_minutes": None, "cost_per_accepted_field_usd": None,
        "cost_note": "Local compute and review time not measured"}, indent=2))


if __name__ == "__main__":
    main()

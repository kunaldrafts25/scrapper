"""Synthetic one-link regression corpus. It does not measure real-site accuracy."""
import json
import math
import statistics
import time
from pathlib import Path

from verified_extraction.automatic import build_automatic_result
from verified_extraction.fetch import Page


def run() -> dict:
    corpus = json.loads(Path(__file__).with_name("one_link_cases.json").read_text(encoding="utf-8"))
    rows = []
    for case in corpus["cases"]:
        page = Page("https://example.org/page", case["html"], "2026-09-30T00:00:00Z", [])
        started = time.perf_counter()
        actual = build_automatic_result([page], [], False)
        elapsed_ms = (time.perf_counter() - started) * 1000
        facts = {(item["group"], item["key"]): item for item in actual["facts"]}
        checks = [actual["page_type"]["value"] == case["expected_type"]]
        checks.extend(facts.get((group, key), {}).get("value") == value
                      for group, key, value in case.get("expected", []))
        checks.extend(facts.get((group, key), {}).get("state") == state
                      for group, key, state in case.get("expected_states", []))
        checks.extend(not any(item.get("value") == value for item in actual["facts"])
                      for value in case.get("forbidden_values", []))
        rows.append({"id": case["id"], "passed": all(checks), "checks": len(checks),
                     "offline_extract_ms": round(elapsed_ms, 3),
                     "found": sum(item["state"] == "found_in_source" for item in actual["facts"])})
    elapsed = sorted(row["offline_extract_ms"] for row in rows)
    return {"fixture_version": corpus["fixture_version"], "network_calls": 0,
            "cases": len(rows), "passed": sum(row["passed"] for row in rows),
            "checks": sum(row["checks"] for row in rows),
            "offline_extract_p50_ms": round(statistics.median(elapsed), 3),
            "offline_extract_p95_ms": elapsed[math.ceil(.95 * len(elapsed)) - 1],
            "per_case": rows}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))

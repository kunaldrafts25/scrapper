import csv
from pathlib import Path

import pytest

from benchmark.finalize import finalize


def test_finalization_refuses_partial_and_requires_real_measurements():
    with Path("benchmark/candidate_targets.csv").open(newline="", encoding="utf-8-sig") as file:
        inventory = list(csv.DictReader(file))
    for row in inventory:
        row.update(selection_status="APPROVED", access_basis="public page approved by customer",
                   robots_status="ALLOWED", robots_checked_at="2026-10-15",
                   approval_record="customer-scope-1")
    agreement = {"confirmed_by": "customer-1", "confirmed_at": "2026-10-14T12:00:00+00:00",
                 "analyst_hourly_rate_usd": 30,
                 "thresholds": {"min_precision": .95, "min_recall": .5, "min_accepted": 24,
                                "min_time_reduction": .25, "max_cost_per_row_usd": 5.0,
                                "max_access_failures": 2}}
    costs = {row["site_id"]: {"compute_usd": 0.01, "provider_usd": 0,
                              "compute_basis": "metered local CPU", "provider_basis": "no providers used"}
             for row in inventory}
    cases = [{"site": row["site_id"], "split": row["split"], "plan_name": row["plan_name"],
              "seed": row["seed_url"], "live_elapsed_seconds": 1,
              "http_requests_started": 2, "manual_baseline_seconds": 120,
              "manual_baseline_errors": 0, "review_seconds": 20,
              "blind_label_seconds": 30, "assisted_reviews": {"review_sessions": [{}], "reviews": {}},
              "independent_labels": {"reviewer_a": {}, "reviewer_b": {} if row["split"] == "development" else {"fields": {}},
                                     "adjudication": {} if row["split"] == "development" else {"fields": {}}},
              "code_revision": "abc", "capture_date": "2026-10-15T12:00:00+00:00"}
             for row in inventory]
    scored = {"per_case": [{"site": case["site"], "split": case["split"], "fields": {},
                            "latency_seconds": 0.1, "failure": None,
                            "access_failure_codes": [], "reviewed_row_correct": False,
                            "review_seconds": 20, "manual_baseline_seconds": 120,
                            "manual_baseline_fields": 6, "manual_baseline_errors": 0,
                            "live_elapsed_seconds": 1, "http_requests_started": 2}
                           for case in cases]}
    with pytest.raises(ValueError, match="24 approved"):
        finalize(cases[:-1], scored, inventory, agreement, costs)
    cases[0]["manual_baseline_seconds"] = None
    with pytest.raises(ValueError, match="lacks live"):
        finalize(cases, scored, inventory, agreement, costs)
    cases[0]["manual_baseline_seconds"] = 120
    result = finalize(cases, scored, inventory, agreement, costs)
    assert result["status"] == "finalized"
    assert result["decision"] == "no-go"
    assert result["cost"]["usd_per_correct_reviewed_row"] is None

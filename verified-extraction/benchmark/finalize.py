"""Executable gate for a complete, approved customer-task evaluation."""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from .validate_targets import validate_rows


def finalize(cases: list[dict], scored: dict, inventory: list[dict], agreement: dict,
             costs: dict) -> dict:
    scope = validate_rows(inventory, "execution")
    inventory = [{key.lstrip("\ufeff"): value for key, value in row.items()} for row in inventory]
    by_site = {row["site_id"]: row for row in inventory}
    if len(cases) != 24 or {case["site"] for case in cases} != set(by_site):
        raise ValueError("Finalization requires all 24 approved site outcomes")
    if any(case["split"] != by_site[case["site"]]["split"] or
           case.get("plan_name") != by_site[case["site"]]["plan_name"] or
           case.get("seed") != by_site[case["site"]]["seed_url"] for case in cases):
        raise ValueError("Frozen outcomes differ from the approved task or split")
    if not agreement.get("confirmed_by") or not agreement.get("confirmed_at"):
        raise ValueError("Customer threshold and labor agreement is missing")
    try:
        confirmed = datetime.fromisoformat(agreement["confirmed_at"])
        rate = float(agreement["analyst_hourly_rate_usd"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Agreed labor rate or confirmation time is invalid") from exc
    if rate <= 0 or confirmed.tzinfo is None:
        raise ValueError("Agreed labor rate and timestamp must be positive and timezone-aware")
    thresholds = {"min_precision": .95, "min_recall": .50, "min_accepted": 24,
                  "min_time_reduction": .25, "max_cost_per_row_usd": 5.0,
                  "max_access_failures": 2}
    if agreement.get("thresholds") != thresholds:
        raise ValueError("Pre-registered thresholds need explicit customer confirmation")
    if set(costs) != set(by_site):
        raise ValueError("Measured cost ledger needs every approved site")
    for case in cases:
        if (case.get("live_elapsed_seconds") is None or case.get("http_requests_started") is None or
            case.get("manual_baseline_seconds") is None or case.get("review_seconds") is None or
            case.get("manual_baseline_errors") is None or not case.get("assisted_reviews") or
            case.get("corrected_row_errors") is None or not case.get("corrected_row") or
            not case.get("independent_labels") or not case.get("code_revision") or
            not case.get("capture_date") or case.get("blind_label_seconds") is None or
            not isinstance(case.get("access_policy_violations"), list)):
            raise ValueError(f"Site {case['site']} lacks live, blind, review or baseline measurements")
        if case["split"] == "held_out":
            labels = case["independent_labels"]
            if not labels.get("reviewer_b") or not labels.get("adjudication"):
                raise ValueError("Held-out site lacks two blind labels and adjudication")
            captured = datetime.fromisoformat(case["capture_date"])
            if captured.tzinfo is None or confirmed > captured:
                raise ValueError("Threshold and rate agreement must precede held-out capture")
        ledger = costs[case["site"]]
        if any(key not in ledger or isinstance(ledger[key], bool) or
               not isinstance(ledger[key], (int, float)) or ledger[key] < 0
               for key in ("compute_usd", "provider_usd")):
            raise ValueError("Measured compute and provider charges need nonnegative numbers")
        if not ledger.get("compute_basis") or not ledger.get("provider_basis"):
            raise ValueError("Measured charges need a compute and provider basis")
        if ledger["provider_usd"] > float(by_site[case["site"]]["max_provider_spend_usd"]):
            raise ValueError("Measured provider spend exceeds approved site ceiling")
        allowed = set(by_site[case["site"]]["allowed_hostnames"].split(";"))
        for requested, page in case.get("pages", {}).items():
            urls = [requested, page.get("final_url", requested), *page.get("redirects", [])]
            if any(urlsplit(url).hostname not in allowed for url in urls):
                raise ValueError("Frozen page or redirect escaped approved host scope")
    held = [row for row in scored["per_case"] if row["split"] == "held_out"]
    if len(held) != 12:
        raise ValueError("Finalization requires 12 held-out scored sites")
    from .run import summarize
    metrics = summarize(held)
    assisted_verdict_errors = 0
    for row in scored["per_case"]:
        case = next(item for item in cases if item["site"] == row["site"])
        reviews = case["assisted_reviews"].get("reviews", {})
        if set(reviews) != set(row["fields"]):
            raise ValueError(f"Site {row['site']} lacks machine-assisted verdicts for every field")
        sessions = case["assisted_reviews"].get("review_sessions", [])
        if not sessions or {session.get("field_name") for session in sessions} != set(row["fields"]):
            raise ValueError(f"Site {row['site']} lacks assisted review sessions for every field")
        if row["split"] == "held_out":
            assisted_verdict_errors += sum(
                (review.get("verdict") == "correct") != bool(row["fields"][name]["correct"])
                for name, review in reviews.items())
    denominator = sum(case["corrected_row_errors"] == 0 and
                      all(field.get("state") == "verified" for field in case["corrected_row"]["fields"].values())
                      for case in cases)
    labor_seconds = sum(case["review_seconds"] + case["blind_label_seconds"] +
                        case["manual_baseline_seconds"] for case in cases)
    labor_usd = labor_seconds / 3600 * rate
    compute_usd = sum(row["compute_usd"] for row in costs.values())
    provider_usd = sum(row["provider_usd"] for row in costs.values())
    total_usd = labor_usd + compute_usd + provider_usd
    cost_per_row = round(total_usd / denominator, 4) if denominator else None
    d = metrics["denominators"]
    robots_scope_stops = [(row["site"], code) for row in scored["per_case"]
                          for code in row.get("access_failure_codes", [])
                          if code in {"ROBOTS_DENIED", "ROBOTS_UNAVAILABLE", "OUT_OF_SCOPE"}]
    reported_violations = [(case["site"], violation) for case in cases
                           for violation in case.get("access_policy_violations", [])]
    critical_names = {"named_plan", "listed_price", "currency", "billing_period"}
    critical_wrong = [(row["site"], name) for row in held for name, field in row["fields"].items()
                      if name in critical_names and field["actual_state"] == "verified" and not field["correct"]]
    checks = {
        "critical_claims": not critical_wrong,
        "evidence_valid": d["evidence_checked"] == d["evidence_valid"],
        "precision": metrics["field_precision"] is not None and metrics["field_precision"] >= .95,
        "accepted_count": d["accepted"] >= 24,
        "recall": metrics["field_recall"] is not None and metrics["field_recall"] >= .50,
        "review_time": metrics["paired_assisted_time_reduction"] is not None and
                       metrics["paired_assisted_time_reduction"] >= .25,
        "review_error": sum(row["corrected_row_errors"] for row in held) <=
                        sum(row["manual_baseline_errors"] for row in held),
        "robots_scope_policy": not robots_scope_stops and not reported_violations,
        "access_failures": d["access_failures"] <= 2,
        "request_bounds": all(row["http_requests_started"] <= int(by_site[row["site"]]["max_http_requests"])
                              for row in cases),
        "cost_ceiling": cost_per_row is not None and cost_per_row <= 5.0,
    }
    return {"status": "finalized", "decision": "go" if all(checks.values()) else "no-go",
            "checks": checks, "critical_wrong_accepted": critical_wrong,
            "assisted_verdict_errors": assisted_verdict_errors,
            "assisted_verdict_fields": sum(len(row["fields"]) for row in held),
            "assisted_verdict_error_rate": round(assisted_verdict_errors / sum(len(row["fields"]) for row in held), 4),
            "corrected_row_errors": sum(row["corrected_row_errors"] for row in held),
            "corrected_row_fields": sum(row["corrected_row_fields"] for row in held),
            "corrected_row_error_rate": metrics["corrected_row_error_rate"],
            "robots_scope_stops": robots_scope_stops,
            "reported_policy_violations": reported_violations,
            "cost": {"labor_usd": round(labor_usd, 4), "compute_usd": round(compute_usd, 4),
                     "provider_usd": round(provider_usd, 4), "total_usd": round(total_usd, 4),
                     "correct_reviewed_rows": denominator, "usd_per_correct_reviewed_row": cost_per_row},
            "held_out_summary": metrics, "approved_scope": scope}

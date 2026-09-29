import csv
from pathlib import Path

import pytest

from benchmark.validate_targets import validate_rows


def test_target_inventory_requires_approval_and_site_split():
    with Path("benchmark/approved_targets.template.csv").open(newline="", encoding="utf-8") as file:
        with pytest.raises(ValueError):
            validate_rows(list(csv.DictReader(file)))
    rows = []
    for index in range(24):
        host = f"vendor-{index}.example"
        rows.append({"site_id": f"site-{index}", "seed_url": f"https://{host}/",
            "vendor_name": f"Vendor {index}", "category": "workflow", "plan_name": "Pro",
            "allowed_hostnames": host, "access_basis": "public page reviewed by customer",
            "robots_status": "ALLOWED", "robots_checked_at": "2026-09-29", "approval_record": "approval-1",
            "selection_status": "APPROVED", "max_pages": "1", "max_depth": "0",
            "deadline_seconds": "20", "date_window_start": "2026-10-01", "date_window_end": "2026-10-01",
            "retention_days": "7", "provider_accounts": "NONE", "data_destinations": "LOCAL_ONLY",
            "expected_impact": "read-only requests",
            "split": "development" if index < 12 else "held_out",
            "max_http_requests": "10", "max_provider_spend_usd": "0"})
    assert validate_rows(rows)["maximum_http_requests"] == 240
    rows[-1]["allowed_hostnames"] = "different.example"
    with pytest.raises(ValueError, match="Seed hostname"):
        validate_rows(rows)


def test_candidate_inventory_is_frozen_by_site_but_not_approved():
    with Path("benchmark/candidate_targets.csv").open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 24
    assert sum(row["split"] == "development" for row in rows) == 12
    assert sum(row["split"] == "held_out" for row in rows) == 12
    assert len({row["allowed_hostnames"] for row in rows}) == 24
    proposal = validate_rows(rows, "proposal")
    assert proposal["sites"] == 24
    assert proposal["maximum_http_requests"] == 480
    assert proposal["execution_ready"] is False
    assert len(proposal["pending_site_fields"]) == 24
    with pytest.raises(ValueError, match="Execution requires recorded approval"):
        validate_rows(rows, "execution")

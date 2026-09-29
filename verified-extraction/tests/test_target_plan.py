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
            "plan_name": "Pro", "allowed_hostnames": host, "access_basis": "customer approval",
            "robots_checked_at": "2026-01-01", "approval_record": "approval-1",
            "split": "development" if index < 12 else "held_out",
            "max_http_requests": "5", "max_provider_spend_usd": "0"})
    assert validate_rows(rows)["maximum_http_requests"] == 120
    rows[-1]["allowed_hostnames"] = "different.example"
    with pytest.raises(ValueError, match="Seed hostname"):
        validate_rows(rows)

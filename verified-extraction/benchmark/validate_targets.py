"""Validate a proposed target inventory without DNS queries or HTTP requests."""
import argparse
import csv
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from verified_extraction.security import canonical_url, FetchError


def validate_rows(rows: list[dict]) -> dict:
    if len(rows) != 24:
        raise ValueError("Exactly 24 approved site rows are required")
    sites = set()
    hosts = set()
    splits = {"development": 0, "held_out": 0}
    requests = 0
    spend = 0.0
    for row in rows:
        required = ("site_id", "vendor_name", "category", "seed_url", "plan_name", "allowed_hostnames",
            "access_basis", "robots_status", "robots_checked_at", "approval_record", "max_pages",
            "max_depth", "deadline_seconds", "max_http_requests", "date_window_start",
            "date_window_end", "retention_days", "provider_accounts", "data_destinations",
            "max_provider_spend_usd", "expected_impact", "selection_status")
        if any(not row.get(key) or any(marker in row[key].upper() for marker in
               ("PENDING", "REPLACE", "UNVERIFIED", "NOT_CHECKED")) for key in required):
            raise ValueError("Every target needs a completed plan, access, robots and approval record")
        if row["selection_status"] != "APPROVED" or row["robots_status"] != "ALLOWED":
            raise ValueError("Only approved robots-allowed targets can enter a live run")
        try:
            seed = canonical_url(row["seed_url"])
        except FetchError as exc:
            raise ValueError(f"Invalid seed URL for {row['site_id']}") from exc
        host = urlsplit(seed).hostname
        allowed = row["allowed_hostnames"].split(";")
        if host not in allowed or any(not item or item != item.strip() for item in allowed):
            raise ValueError("Seed hostname must be in the exact allowed-host list")
        if row["site_id"] in sites or host in hosts:
            raise ValueError("Sites and hostnames must be distinct across splits")
        sites.add(row["site_id"])
        hosts.add(host)
        if row["split"] not in splits:
            raise ValueError("Invalid split")
        splits[row["split"]] += 1
        try:
            budget = int(row["max_http_requests"])
            cost = float(row["max_provider_spend_usd"])
            max_pages = int(row["max_pages"])
            max_depth = int(row["max_depth"])
            deadline = int(row["deadline_seconds"])
            retention = int(row["retention_days"])
            start = date.fromisoformat(row["date_window_start"])
            end = date.fromisoformat(row["date_window_end"])
            date.fromisoformat(row["robots_checked_at"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid request, spend, retention or date value") from exc
        if (not 1 <= max_pages <= 8 or not 0 <= max_depth <= 2 or not 2 <= deadline <= 45 or
            not 1 <= budget <= 50 or budget < 5 * (max_pages + len(allowed)) or cost < 0 or
            not 1 <= retention <= 7 or end < start or (end - start).days > 1):
            raise ValueError("Target limits do not cover the worst-case redirect and robots budget")
        if cost > 0 and row["provider_accounts"] == "NONE":
            raise ValueError("Paid provider spend needs a named account")
        requests += budget
        spend += cost
    if splits != {"development": 12, "held_out": 12}:
        raise ValueError("Use 12 development and 12 held-out hostnames")
    return {"sites": len(rows), "splits": splits, "maximum_http_requests": requests,
            "maximum_provider_spend_usd": round(spend, 2), "network_calls": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args()
    with args.csv.open(newline="", encoding="utf-8") as file:
        result = validate_rows(list(csv.DictReader(file)))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

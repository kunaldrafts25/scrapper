"""Validate a proposed target inventory without DNS queries or HTTP requests."""
import argparse
import csv
import json
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
        if any(not row.get(key) or "PENDING" in row[key].upper() or "REPLACE" in row[key].upper()
               for key in ("site_id", "seed_url", "plan_name", "allowed_hostnames", "access_basis",
                           "robots_checked_at", "approval_record")):
            raise ValueError("Every target needs a completed URL, plan, access and approval record")
        try:
            seed = canonical_url(row["seed_url"])
        except FetchError as exc:
            raise ValueError(f"Invalid seed URL for {row['site_id']}") from exc
        host = urlsplit(seed).hostname
        if host not in row["allowed_hostnames"].split(";"):
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
        except (TypeError, ValueError) as exc:
            raise ValueError("Request and spend limits must be numeric") from exc
        if budget < 1 or cost < 0:
            raise ValueError("Request and spend limits must be nonnegative")
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

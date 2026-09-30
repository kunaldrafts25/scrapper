"""Validate the frozen one-link proposal without making any network request."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

TYPES = {"article", "organization_service", "product", "saas_pricing"}


def validate(rows: list[dict], mode: str) -> dict:
    if mode not in {"proposal", "execution"} or len(rows) != 24:
        raise ValueError("Expected 24 rows and proposal or execution mode")
    counts = Counter((row["split"], row["page_type"]) for row in rows)
    if any(counts[split, kind] != 3 for split in ("development", "held_out") for kind in TYPES):
        raise ValueError("Each split needs three pages of every supported type")
    hosts = set()
    for row in rows:
        parsed = urlsplit(row["seed_url"])
        if parsed.scheme != "https" or parsed.hostname != row["allowed_host"] or parsed.username or parsed.password:
            raise ValueError("Seed URL must use HTTPS and the exact allowed hostname")
        if parsed.hostname in hosts or row["max_gets"] != "10":
            raise ValueError("Duplicate hostname or request ceiling changed")
        hosts.add(parsed.hostname)
        if mode == "execution" and (row["approval_record"] == "PENDING" or
                                    row["robots_status"] != "ALLOWED" or
                                    row["access_basis"] == "UNVERIFIED_PUBLIC_PAGE"):
            raise ValueError("Execution requires exact approval, allowed robots, and access basis")
    return {"sites": len(rows), "splits": dict(Counter(row["split"] for row in rows)),
            "maximum_http_requests": 240, "network_calls": 0, "mode": mode,
            "execution_ready": mode == "execution"}


def validate_approval(approval: dict, csv_bytes: bytes, now: datetime | None = None) -> None:
    expected = {"scope_sha256": hashlib.sha256(csv_bytes).hexdigest(),
                "max_gets_per_host": 10, "max_gets_overall": 240,
                "window": "2026-10-15 09:00-11:00 Asia/Kolkata",
                "data_destination": "benchmark/local/one_link/ and benchmark/local/one_link.sqlite3",
                "retention_days": 7, "provider_spend_ceiling_usd": 0,
                "accounts": "none", "site_impact": "read-only GET; no assets or scripts"}
    if any(approval.get(key) != value for key, value in expected.items()) or not all(
            approval.get(key) for key in ("approver", "approved_at", "authorization_text")):
        raise ValueError("Approval record does not cover the exact scope")
    current = (now or datetime.now(ZoneInfo("Asia/Kolkata"))).astimezone(ZoneInfo("Asia/Kolkata"))
    if not (current.year == 2026 and current.month == 10 and current.day == 15 and
            9 <= current.hour < 11):
        raise ValueError("Outside the approved date window")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="benchmark/one_link_targets.csv")
    parser.add_argument("--mode", choices=["proposal", "execution"], required=True)
    parser.add_argument("--approval", default="benchmark/local/one_link/approval_record.json")
    args = parser.parse_args()
    source_path = Path(args.csv)
    with source_path.open(newline="", encoding="utf-8") as source:
        result = validate(list(csv.DictReader(source)), args.mode)
    if args.mode == "execution":
        approval = json.loads(Path(args.approval).read_text(encoding="utf-8"))
        validate_approval(approval, source_path.read_bytes())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

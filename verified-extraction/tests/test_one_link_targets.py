import csv
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import pytest

from benchmark.validate_one_link_targets import validate, validate_approval


def rows():
    with Path("benchmark/one_link_targets.csv").open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def test_one_link_proposal_frozen_and_execution_pending():
    assert validate(rows(), "proposal")["maximum_http_requests"] == 240
    with pytest.raises(ValueError, match="approval"):
        validate(rows(), "execution")


def test_wrong_host_or_changed_budget_is_rejected():
    entries = rows()
    entries[0]["allowed_host"] = "other.example"
    with pytest.raises(ValueError, match="exact allowed hostname"):
        validate(entries, "proposal")
    entries = rows()
    entries[0]["max_gets"] = "20"
    with pytest.raises(ValueError, match="ceiling"):
        validate(entries, "proposal")


def test_approval_record_must_bind_exact_csv_and_window():
    proposal = Path("benchmark/one_link_targets.csv").read_bytes()
    approval = json.loads(Path("benchmark/one_link_approval.template.json").read_text(encoding="utf-8"))
    approval.update(scope_sha256=hashlib.sha256(proposal).hexdigest(), approver="customer",
                    approved_at="2026-10-01T00:00:00Z", authorization_text="approved exact scope")
    during_window = datetime(2026, 10, 15, 9, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    validate_approval(approval, proposal, during_window)
    with pytest.raises(ValueError, match="exact scope"):
        validate_approval(approval, proposal + b"changed", during_window)
    approval["window"] = "another date"
    with pytest.raises(ValueError, match="exact scope"):
        validate_approval(approval, proposal, during_window)
    approval["window"] = "2026-10-15 09:00-11:00 Asia/Kolkata"
    with pytest.raises(ValueError, match="date window"):
        validate_approval(approval, proposal, datetime(2026, 9, 30, 9, 30,
            tzinfo=ZoneInfo("Asia/Kolkata")))

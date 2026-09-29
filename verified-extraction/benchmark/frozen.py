"""Offline capture export and blind-label validation. No network operations."""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path

from verified_extraction.extract import visible_blocks, parse_scalar
from verified_extraction.fetch import decode_html
from verified_extraction.store import Store
from .ground_truth import capture_index, validate_claim


def export_local(store: Store, tenant: str, job_id: str, output: Path, site_id: str,
                 split: str, category: str, plan_name: str, permission_note: str) -> dict:
    result = store.get(tenant, job_id)
    request = store.get_request(tenant, job_id)
    if not result or not request:
        raise ValueError("Job or stored request is unavailable; older jobs need a new approved capture")
    if split not in {"development", "held_out"} or not permission_note.strip() or not plan_name.strip():
        raise ValueError("Split, named plan and permission note are required")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[1], text=True).strip()
    output.mkdir(parents=True, exist_ok=False)
    captures_dir = output / "captures"
    captures_dir.mkdir()
    pages = {}
    for row in result["pages"]:
        requested_url = row["requested_url"]
        if row["status"] == "error":
            pages[requested_url] = {"error": row["error_code"]}
            continue
        digest = row["snapshot_hash"]
        capture = store.snapshot_record(tenant, job_id, digest)
        if not capture or capture["raw"] is None or hashlib.sha256(capture["raw"]).hexdigest() != digest:
            raise ValueError("Capture bytes are unavailable or hash-mismatched")
        raw = capture["raw"]
        encoding = row.get("encoding") or capture["encoding"]
        (captures_dir / f"{digest}.bin").write_bytes(raw)
        (captures_dir / f"{digest}.txt").write_text(raw.decode(encoding, "replace"), encoding="utf-8")
        pages[requested_url] = {"raw_base64": base64.b64encode(raw).decode("ascii"),
            "content_type": row.get("content_type") or capture["content_type"] or "text/html",
            "encoding": encoding, "fetched_at": row["fetched_at"],
            "final_url": row["url"], "redirects": row.get("redirects", []),
            "snapshot_hash": digest}
    manifest = {"fixture_version": "real-2.0", "site_id": site_id, "split": split,
        "category": category, "seed": request["url"], "schema": request["schema"],
        "options": request.get("options", {}), "page_hints": request.get("page_hints", []),
        "allowed_hostnames": request.get("allowed_hostnames", []),
        "capture_date": result["observed_at"], "permission_note": permission_note,
        "source_job_id": job_id, "pages": pages, "plan_name": plan_name,
        "code_revision": revision, "live_elapsed_seconds": result["usage"].get("elapsed_seconds"),
        "http_requests_started": result["usage"].get("http_requests_started"),
        "preflight_http_requests_started": None,
        "access_policy_violations": None}
    template = {"label_schema_version": "2.0", "site_id": site_id, "split": split,
        "labeling_mode": "blind", "reviewer_id": None, "plan_name": plan_name,
        "fields": {name: {"state": None, "value": None, "plan_name": plan_name,
                          "unit": None, "currency": None, "billing_period": None,
                          "conditions": None, "ambiguity": None, "judgment": "explicit",
                          "rationale": None, "evidence": [], "reviewer_time_seconds": None}
                   for name in request["schema"]["properties"]}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (output / "labels.template.json").write_text(json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8")
    baseline = {"baseline_schema_version": "2.0", "site_id": site_id, "analyst_id": None,
        "elapsed_seconds": None, "fields": {name: {"state": None, "value": None,
            "plan_name": plan_name, "unit": None, "currency": None, "billing_period": None,
            "conditions": None, "ambiguity": None, "judgment": "explicit",
            "rationale": None, "evidence": []}
            for name in request["schema"]["properties"]}}
    (output / "manual_baseline.template.json").write_text(
        json.dumps(baseline, indent=2, ensure_ascii=False), encoding="utf-8")
    corrected = {"corrected_row_schema_version": "1.0", "site_id": site_id,
        "source_job_id": job_id, "reviewer_id": None,
        "fields": {name: {"state": None, "value": None, "plan_name": plan_name,
            "unit": None, "currency": None, "billing_period": None,
            "conditions": None, "ambiguity": None, "judgment": "explicit",
            "rationale": None, "evidence": []}
            for name in request["schema"]["properties"]}}
    (output / "corrected_row.template.json").write_text(
        json.dumps(corrected, indent=2, ensure_ascii=False), encoding="utf-8")
    if split == "held_out":
        adjudication = {"adjudication_schema_version": "1.0", "site_id": site_id,
            "reviewer_id": None, "fields": {name: {"decision": None, "reason": None,
                "final": None, "time_spent_seconds": None} for name in request["schema"]["properties"]}}
        (output / "adjudication.template.json").write_text(
            json.dumps(adjudication, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def _validate_labels_v2(manifest: dict, labels: dict) -> dict:
    if manifest.get("fixture_version") != "real-2.0" or labels.get("label_schema_version") != "2.0":
        raise ValueError("Unsupported frozen bundle version")
    if (labels.get("labeling_mode") != "blind" or not labels.get("reviewer_id") or
        labels.get("site_id") != manifest.get("site_id") or labels.get("split") != manifest.get("split") or
        labels.get("plan_name") != manifest.get("plan_name") or "machine_result" in labels):
        raise ValueError("Blind labels must match the site, split and named plan without machine output")
    properties = manifest["schema"]["properties"]
    fields = labels.get("fields", {})
    if set(fields) != set(properties):
        raise ValueError("Every schema field needs a blind label")
    captures = capture_index(manifest)
    expected = {}
    for name, label in fields.items():
        state = label.get("state")
        seconds = label.get("reviewer_time_seconds")
        if state not in {"verified", "missing", "conflicting", "blocked", "unverified"} or \
           isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
            raise ValueError(f"Field {name} needs a state and measured reviewer time")
        if label.get("plan_name") != manifest["plan_name"]:
            raise ValueError(f"Field {name} names the wrong plan")
        if state == "verified":
            expected[name] = validate_claim(manifest, name, label, captures)
        elif state == "conflicting":
            candidates = label.get("candidates")
            if not isinstance(candidates, list) or len(candidates) < 2:
                raise ValueError(f"Field {name} needs at least two supported conflict candidates")
            validated = [validate_claim(manifest, name, {**candidate, "state": "verified"}, captures)
                         for candidate in candidates]
            expected[name] = {"state": "conflicting", "plan_name": manifest["plan_name"],
                              "candidates": validated, "ambiguity": label.get("ambiguity")}
        else:
            if label.get("value") is not None or label.get("evidence"):
                raise ValueError(f"Field {name} cannot contain an unsupported accepted value")
            expected[name] = {"state": state, "plan_name": manifest["plan_name"],
                              "ambiguity": label.get("ambiguity")}
    return expected


def _validate_labels(manifest: dict, labels: dict) -> dict:
    if manifest.get("fixture_version") != "real-1.1" or labels.get("label_schema_version") != "1.1":
        raise ValueError("Unsupported frozen bundle version")
    if labels.get("labeling_mode") != "blind" or not labels.get("reviewer_id"):
        raise ValueError("Independent blind reviewer identity is required")
    if labels.get("site_id") != manifest.get("site_id") or labels.get("split") != manifest.get("split"):
        raise ValueError("Labels do not match the site and split")
    properties = manifest["schema"]["properties"]
    fields = labels.get("fields", {})
    if set(fields) != set(properties):
        raise ValueError("Every schema field must have an independent label")
    captures = {}
    for requested_url, page in manifest["pages"].items():
        if "error" in page:
            continue
        raw = base64.b64decode(page["raw_base64"], validate=True)
        digest = hashlib.sha256(raw).hexdigest()
        if digest != page["snapshot_hash"]:
            raise ValueError("Frozen capture hash mismatch")
        decoded, _, _ = decode_html(raw, page["content_type"])
        captures[(page["final_url"], digest)] = {text for text, _ in visible_blocks(decoded)}
    expected = {}
    allowed_states = {"verified", "missing", "conflicting", "blocked", "unverified"}
    def check_source(name: str, item: dict):
        key = (item.get("source_url"), item.get("snapshot_hash"))
        if item.get("value") is None or not item.get("excerpt") or key not in captures or not item.get("raw_value"):
            raise ValueError(f"Field {name} lacks source-backed expected evidence")
        excerpt = item["excerpt"]
        if excerpt not in captures[key]:
            raise ValueError(f"Field {name} excerpt is absent from a visible claim in its frozen capture")
        label = properties[name].get("title") or name.replace("_", " ")
        label_pos = excerpt.casefold().find(label.casefold())
        value_pos = excerpt.find(item["raw_value"])
        tail = excerpt[label_pos + len(label):].strip().lstrip(":-– ").strip() if label_pos >= 0 else ""
        if label_pos < 0 or value_pos < label_pos + len(label) or tail != item["raw_value"]:
            raise ValueError(f"Field {name} value is not tied to its label in the excerpt")
        parsed = parse_scalar(item["raw_value"], properties[name]["type"])
        if parsed is None or parsed != (item["value"], item.get("unit"), item.get("currency")):
            raise ValueError(f"Field {name} value, unit or currency disagrees with its captured claim")
    for name, label in fields.items():
        state = label.get("state")
        if state not in allowed_states:
            raise ValueError(f"Field {name} has no valid independent state")
        seconds = label.get("reviewer_time_seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
            raise ValueError(f"Field {name} needs measured reviewer time")
        if label.get("judgment") not in {"literal", "interpretation"}:
            raise ValueError(f"Field {name} needs a judgment type")
        if label["judgment"] == "interpretation":
            if state == "verified" or not label.get("interpretation_note"):
                raise ValueError(f"Field {name} interpretation needs an explanation and cannot be verified without literal support")
        expected[name] = {key: label[key] for key in ("state", "value", "unit", "currency", "source_url",
                          "snapshot_hash", "excerpt") if label.get(key) is not None}
        if state == "verified":
            check_source(name, label)
        if state == "conflicting":
            candidates = label.get("candidates")
            if not isinstance(candidates, list) or len(candidates) < 2:
                raise ValueError(f"Field {name} requires two labeled conflict candidates")
            for candidate in candidates:
                check_source(name, candidate)
            expected[name]["candidates"] = candidates
    return expected


def _score_human_row(manifest: dict, expected: dict, proposed_fields: dict) -> dict[str, str]:
    """Compare a source-cited human row with blind truth without requiring the same excerpt."""
    captures = capture_index(manifest)
    errors = {}
    for name, truth in expected.items():
        proposed = proposed_fields[name]
        if proposed.get("plan_name") != manifest["plan_name"]:
            errors[name] = "wrong named plan"
        elif proposed.get("state") != truth["state"]:
            errors[name] = "wrong state"
        elif proposed.get("state") == "verified":
            try:
                validated = validate_claim(manifest, name, proposed, captures)
            except (ValueError, KeyError, TypeError) as exc:
                errors[name] = f"invalid cited evidence: {exc}"
                continue
            if any(validated.get(key) != truth.get(key) for key in
                   ("value", "unit", "currency", "billing_period", "plan_name", "conditions")):
                errors[name] = "wrong value or plan context"
        elif proposed.get("state") == "conflicting":
            candidates = proposed.get("candidates")
            if not isinstance(candidates, list) or len(candidates) < 2:
                errors[name] = "conflict needs two cited candidates"
                continue
            try:
                validated = [validate_claim(manifest, name, {**item, "state": "verified"}, captures)
                             for item in candidates]
            except (ValueError, KeyError, TypeError) as exc:
                errors[name] = f"invalid conflict evidence: {exc}"
                continue
            def signature(item):
                return (item.get("value"), item.get("unit"), item.get("currency"),
                        item.get("billing_period"), item.get("plan_name"), item.get("conditions"))
            if sorted(map(str, map(signature, validated))) != sorted(map(str, map(signature, truth.get("candidates", [])))):
                errors[name] = "wrong conflict candidates"
        elif proposed.get("value") is not None or proposed.get("evidence"):
            errors[name] = "unsupported value or evidence for nonverified state"
    return errors


def load_labeled_case(manifest_path: Path, labels_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    modern = manifest.get("fixture_version") == "real-2.0"
    validate = _validate_labels_v2 if modern else _validate_labels
    if manifest.get("split") == "held_out":
        folder = manifest_path.parent
        first = json.loads((folder / "labels.reviewer-a.json").read_text(encoding="utf-8"))
        second = json.loads((folder / "labels.reviewer-b.json").read_text(encoding="utf-8"))
        left = validate(manifest, first)
        right = validate(manifest, second)
        if first["reviewer_id"] == second["reviewer_id"]:
            raise ValueError("Two distinct blind reviewers are required")
        adjudication = json.loads((folder / "adjudication.json").read_text(encoding="utf-8"))
        if (adjudication.get("adjudication_schema_version") != "1.0" or
            adjudication.get("site_id") != manifest["site_id"] or
            adjudication.get("reviewer_id") in {None, first["reviewer_id"], second["reviewer_id"]} or
            set(adjudication.get("fields", {})) != set(left)):
            raise ValueError("A separate complete adjudication record is required")
        expected = {}
        for name, decision in adjudication["fields"].items():
            choice = decision.get("decision")
            if choice not in {"agree", "select_a", "select_b", "resolved"}:
                raise ValueError(f"Field {name} has no adjudication decision")
            if choice == "agree":
                first_claim = {key: value for key, value in first["fields"][name].items() if key != "reviewer_time_seconds"}
                second_claim = {key: value for key, value in second["fields"][name].items() if key != "reviewer_time_seconds"}
                if first_claim != second_claim:
                    raise ValueError(f"Field {name} disagrees and cannot be marked agreed")
                expected[name] = left[name]
            else:
                if not decision.get("reason"):
                    raise ValueError(f"Field {name} disagreement needs a reason")
                if choice == "resolved":
                    final_label = {"label_schema_version": "2.0" if modern else "1.1",
                        "site_id": manifest["site_id"], "plan_name": manifest.get("plan_name"),
                        "split": manifest["split"], "labeling_mode": "blind",
                        "reviewer_id": adjudication["reviewer_id"], "fields": {
                            **first["fields"], name: decision.get("final")}}
                    expected[name] = validate(manifest, final_label)[name]
                else:
                    expected[name] = left[name] if choice == "select_a" else right[name]
            seconds = decision.get("time_spent_seconds")
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
                raise ValueError(f"Field {name} needs adjudication time")
        blind_label_seconds = sum(float(item["reviewer_time_seconds"]) for item in first["fields"].values()) + \
            sum(float(item["reviewer_time_seconds"]) for item in second["fields"].values()) + \
            sum(float(item["time_spent_seconds"]) for item in adjudication["fields"].values())
        label_a, label_b = first, second
    else:
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
        expected = validate(manifest, labels)
        blind_label_seconds = sum(float(item["reviewer_time_seconds"]) for item in labels["fields"].values())
        label_a, label_b, adjudication = labels, None, None
    assisted_path = manifest_path.with_name("assisted_reviews.json")
    assisted = None
    assisted_seconds = None
    if assisted_path.exists():
        assisted = json.loads(assisted_path.read_text(encoding="utf-8"))
        if (assisted.get("label_schema_version") != "1.1" or
            assisted.get("job_id") != manifest.get("source_job_id") or
            not isinstance(assisted.get("review_sessions"), list)):
            raise ValueError("Assisted review export does not match the frozen job")
        sessions = assisted["review_sessions"]
        if any(row.get("stopped_at") is None or not isinstance(row.get("elapsed_seconds"), (int, float)) or
               row["elapsed_seconds"] < 0 for row in sessions):
            raise ValueError("Assisted review sessions must be complete")
        ordered = sorted(sessions, key=lambda row: row["started_at"])
        if any(second["started_at"] < first["stopped_at"] for first, second in zip(ordered, ordered[1:])):
            raise ValueError("Assisted review sessions overlap")
        assisted_seconds = sum(row["elapsed_seconds"] for row in sessions)
    baseline_path = manifest_path.with_name("manual_baseline.json")
    baseline_seconds = baseline_errors = baseline_fields = None
    baseline_error_details = None
    baseline = None
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        seconds = baseline.get("elapsed_seconds")
        if (baseline.get("baseline_schema_version") != ("2.0" if modern else "1.0") or
            baseline.get("site_id") != manifest["site_id"] or not baseline.get("analyst_id") or
            baseline.get("analyst_id") in {label_a.get("reviewer_id"),
                                           label_b.get("reviewer_id") if label_b else None} or
            isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0 or
            set(baseline.get("fields", {})) != set(expected)):
            raise ValueError("Manual baseline needs a separate analyst, elapsed time and every field")
        baseline_seconds = seconds
        baseline_fields = len(expected)
        if modern:
            baseline_error_details = _score_human_row(manifest, expected, baseline["fields"])
            baseline_errors = len(baseline_error_details)
        else:
            baseline_errors = sum(any(baseline["fields"][name].get(key) != label.get(key)
                                      for key in ("state", "value", "unit", "currency", "source_url", "snapshot_hash", "excerpt"))
                                  for name, label in expected.items())
    corrected_path = manifest_path.with_name("corrected_row.json")
    corrected = None
    corrected_errors = corrected_fields = None
    corrected_error_details = None
    if corrected_path.exists():
        if not modern or assisted is None:
            raise ValueError("Corrected final row requires modern labels and assisted review")
        corrected = json.loads(corrected_path.read_text(encoding="utf-8"))
        reviewer = corrected.get("reviewer_id")
        session_reviewers = {item.get("reviewer_id") for item in assisted["review_sessions"]}
        if (corrected.get("corrected_row_schema_version") != "1.0" or
            corrected.get("site_id") != manifest["site_id"] or
            corrected.get("source_job_id") != manifest.get("source_job_id") or
            not reviewer or reviewer not in session_reviewers or
            reviewer in {label_a.get("reviewer_id"), label_b.get("reviewer_id") if label_b else None} or
            set(corrected.get("fields", {})) != set(expected)):
            raise ValueError("Corrected final row needs the assisted reviewer, job and all fields")
        corrected_error_details = _score_human_row(manifest, expected, corrected["fields"])
        corrected_errors = len(corrected_error_details)
        corrected_fields = len(expected)
    site = manifest["site_id"]
    return {"id": site, "site": site, "split": manifest["split"], "category": manifest["category"],
        "seed": manifest["seed"], "schema": manifest["schema"], "options": manifest["options"],
        "page_hints": manifest["page_hints"], "allowed_hostnames": manifest["allowed_hostnames"],
        "pages": manifest["pages"], "expected": expected, "review_seconds": assisted_seconds,
        "blind_label_seconds": blind_label_seconds, "assisted_reviews": assisted,
        "permission_note": manifest["permission_note"],
        "plan_name": manifest.get("plan_name"), "code_revision": manifest.get("code_revision"),
        "capture_date": manifest.get("capture_date"),
        "live_elapsed_seconds": manifest.get("live_elapsed_seconds"),
        "http_requests_started": manifest.get("http_requests_started"),
        "preflight_http_requests_started": manifest.get("preflight_http_requests_started"),
        "access_policy_violations": manifest.get("access_policy_violations"),
        "manual_baseline_seconds": baseline_seconds, "manual_baseline_errors": baseline_errors,
        "manual_baseline_fields": baseline_fields, "manual_baseline": baseline,
        "manual_baseline_error_details": baseline_error_details,
        "corrected_row": corrected, "corrected_row_errors": corrected_errors,
        "corrected_row_fields": corrected_fields,
        "corrected_row_error_details": corrected_error_details,
        "independent_labels": {
            "reviewer_a": label_a, "reviewer_b": label_b, "adjudication": adjudication}}

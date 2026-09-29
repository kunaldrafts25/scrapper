"""Offline capture export and blind-label validation. No network operations."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from verified_extraction.extract import visible_blocks, parse_scalar
from verified_extraction.fetch import decode_html
from verified_extraction.store import Store


def export_local(store: Store, tenant: str, job_id: str, output: Path, site_id: str,
                 split: str, category: str, permission_note: str) -> dict:
    result = store.get(tenant, job_id)
    request = store.get_request(tenant, job_id)
    if not result or not request:
        raise ValueError("Job or stored request is unavailable; older jobs need a new approved capture")
    if split not in {"development", "held_out"} or not permission_note.strip():
        raise ValueError("Split and permission note are required")
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
    manifest = {"fixture_version": "real-1.1", "site_id": site_id, "split": split,
        "category": category, "seed": request["url"], "schema": request["schema"],
        "options": request.get("options", {}), "page_hints": request.get("page_hints", []),
        "allowed_hostnames": request.get("allowed_hostnames", []),
        "capture_date": result["observed_at"], "permission_note": permission_note,
        "source_job_id": job_id, "pages": pages}
    template = {"label_schema_version": "1.1", "site_id": site_id, "split": split,
        "labeling_mode": "blind", "reviewer_id": None,
        "fields": {name: {"state": None, "value": None, "unit": None, "currency": None,
                          "source_url": None, "snapshot_hash": None, "excerpt": None,
                          "raw_value": None, "judgment": "literal", "interpretation_note": None,
                          "reviewer_time_seconds": None} for name in request["schema"]["properties"]}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (output / "labels.template.json").write_text(json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8")
    if split == "held_out":
        adjudication = {"adjudication_schema_version": "1.0", "site_id": site_id,
            "reviewer_id": None, "fields": {name: {"decision": None, "reason": None,
                "final": None, "time_spent_seconds": None} for name in request["schema"]["properties"]}}
        (output / "adjudication.template.json").write_text(
            json.dumps(adjudication, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


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
        if label_pos < 0 or value_pos < label_pos + len(label):
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


def load_labeled_case(manifest_path: Path, labels_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("split") == "held_out":
        folder = manifest_path.parent
        first = json.loads((folder / "labels.reviewer-a.json").read_text(encoding="utf-8"))
        second = json.loads((folder / "labels.reviewer-b.json").read_text(encoding="utf-8"))
        left = _validate_labels(manifest, first)
        right = _validate_labels(manifest, second)
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
                if left[name] != right[name]:
                    raise ValueError(f"Field {name} disagrees and cannot be marked agreed")
                expected[name] = left[name]
            else:
                if not decision.get("reason"):
                    raise ValueError(f"Field {name} disagreement needs a reason")
                if choice == "resolved":
                    final_label = {"label_schema_version": "1.1", "site_id": manifest["site_id"],
                        "split": manifest["split"], "labeling_mode": "blind",
                        "reviewer_id": adjudication["reviewer_id"], "fields": {
                            **first["fields"], name: decision.get("final")}}
                    expected[name] = _validate_labels(manifest, final_label)[name]
                else:
                    expected[name] = left[name] if choice == "select_a" else right[name]
            seconds = decision.get("time_spent_seconds")
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
                raise ValueError(f"Field {name} needs adjudication time")
        review_seconds = sum(float(item["reviewer_time_seconds"]) for item in first["fields"].values()) + \
            sum(float(item["reviewer_time_seconds"]) for item in second["fields"].values()) + \
            sum(float(item["time_spent_seconds"]) for item in adjudication["fields"].values())
        label_a, label_b = first, second
    else:
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
        expected = _validate_labels(manifest, labels)
        review_seconds = sum(float(item["reviewer_time_seconds"]) for item in labels["fields"].values())
        label_a, label_b, adjudication = labels, None, None
    site = manifest["site_id"]
    return {"id": site, "site": site, "split": manifest["split"], "category": manifest["category"],
        "seed": manifest["seed"], "schema": manifest["schema"], "options": manifest["options"],
        "page_hints": manifest["page_hints"], "allowed_hostnames": manifest["allowed_hostnames"],
        "pages": manifest["pages"], "expected": expected, "review_seconds": review_seconds,
        "permission_note": manifest["permission_note"], "independent_labels": {
            "reviewer_a": label_a, "reviewer_b": label_b, "adjudication": adjudication}}

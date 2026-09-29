"""Offline capture export and blind-label validation. No network operations."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from verified_extraction.extract import visible_blocks
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
    manifest = {"fixture_version": "real-1.0", "site_id": site_id, "split": split,
        "category": category, "seed": request["url"], "schema": request["schema"],
        "options": request.get("options", {}), "page_hints": request.get("page_hints", []),
        "allowed_hostnames": request.get("allowed_hostnames", []),
        "capture_date": result["observed_at"], "permission_note": permission_note,
        "source_job_id": job_id, "pages": pages}
    template = {"label_schema_version": "1.0", "site_id": site_id, "split": split,
        "labeling_mode": "blind", "reviewer_id": None,
        "fields": {name: {"state": None, "value": None, "unit": None, "currency": None,
                          "source_url": None, "snapshot_hash": None, "excerpt": None,
                          "reviewer_time_seconds": None} for name in request["schema"]["properties"]}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (output / "labels.template.json").write_text(json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def load_labeled_case(manifest_path: Path, labels_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    if manifest.get("fixture_version") != "real-1.0" or labels.get("label_schema_version") != "1.0":
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
        captures[(page["final_url"], digest)] = decoded
    expected = {}
    allowed_states = {"verified", "missing", "conflicting", "blocked", "unverified"}
    def check_source(name: str, item: dict):
        key = (item.get("source_url"), item.get("snapshot_hash"))
        if item.get("value") is None or not item.get("excerpt") or key not in captures:
            raise ValueError(f"Field {name} lacks source-backed expected evidence")
        decoded = captures[key]
        if item["excerpt"] not in [text for text, _ in visible_blocks(decoded)] and item["excerpt"] not in decoded:
            raise ValueError(f"Field {name} excerpt is absent from its frozen capture")
    for name, label in fields.items():
        state = label.get("state")
        if state not in allowed_states:
            raise ValueError(f"Field {name} has no valid independent state")
        seconds = label.get("reviewer_time_seconds")
        if not isinstance(seconds, (int, float)) or seconds < 0:
            raise ValueError(f"Field {name} needs measured reviewer time")
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
    review_seconds = sum(float(item.get("reviewer_time_seconds") or 0) for item in fields.values())
    site = manifest["site_id"]
    return {"id": site, "site": site, "split": manifest["split"], "category": manifest["category"],
        "seed": manifest["seed"], "schema": manifest["schema"], "options": manifest["options"],
        "page_hints": manifest["page_hints"], "allowed_hostnames": manifest["allowed_hostnames"],
        "pages": manifest["pages"], "expected": expected, "review_seconds": review_seconds,
        "permission_note": manifest["permission_note"]}

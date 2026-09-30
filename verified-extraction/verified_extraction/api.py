from __future__ import annotations

import hmac
import html
import logging
import os
import secrets
import time
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.responses import JSONResponse

from .models import JobRequest, ReviewInput, ReviewSessionInput
from .security import FetchError
from .worker import run_hard
from .extract import source_node_for
from .store import Store

logging.basicConfig(level=logging.INFO, format='%(message)s')
app = FastAPI(title="Verified Extraction", version="0.4.1")
store = Store(os.environ.get("VE_DB", "data/verified_extraction.sqlite3"))


@app.exception_handler(RequestValidationError)
def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": {"code": "INVALID_REQUEST",
        "errors": [{"location": [str(part) for part in item["loc"]], "message": item["msg"]} for item in exc.errors()]}})


def tenant(authorization: str | None = Header(default=None)) -> str:
    # Local MVP: configure VE_KEYS as a JSON object mapping tenant ID to random API key.
    import json
    try:
        keys = json.loads(os.environ.get("VE_KEYS", "{}"))
    except json.JSONDecodeError:
        raise HTTPException(500, detail={"code": "CONFIG_ERROR"})
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, detail={"code": "UNAUTHORIZED"})
    supplied = authorization[7:]
    for tenant_id, key in keys.items():
        if isinstance(key, str) and hmac.compare_digest(supplied, key):
            return tenant_id
    raise HTTPException(401, detail={"code": "UNAUTHORIZED"})


@app.post("/v1/jobs")
def create_job(request: JobRequest, tenant_id: str = Depends(tenant)):
    payload = request.model_dump(by_alias=True)
    fingerprint = store.request_hash(payload)
    owner = __import__("uuid").uuid4().hex
    wait_until = time.monotonic() + request.options.deadline_seconds + 10
    while True:
        state = store.claim(tenant_id, request.idempotency_key, fingerprint, owner)
        if state == "conflict":
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
        if state == "complete":
            existing = store.by_key(tenant_id, request.idempotency_key)
            if existing:
                return existing[1]
            raise HTTPException(503, detail={"code": "INCOMPLETE_JOB"})
        if state == "owner":
            break
        if time.monotonic() >= wait_until:
            raise HTTPException(503, detail={"code": "JOB_IN_PROGRESS"})
        time.sleep(0.05)
    try:
        result, snapshots = run_hard(request, on_tick=lambda: store.renew_claim(
            tenant_id, request.idempotency_key, fingerprint, owner))
        output = result.model_dump()
        store.put(tenant_id, result.job_id, request.idempotency_key, fingerprint, output, snapshots,
                  owner=owner, request_payload=payload)
        return output
    except FetchError as exc:
        store.release_claim(tenant_id, request.idempotency_key, fingerprint, owner)
        status = 504 if exc.code == "DEADLINE" else 503 if exc.code in {"WORKER_FAILED", "CLAIM_LOST"} else 422
        raise HTTPException(status, detail={"code": exc.code, "message": str(exc),
                                            "http_requests_started": exc.http_requests_started})
    except Exception:
        store.release_claim(tenant_id, request.idempotency_key, fingerprint, owner)
        raise


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str, tenant_id: str = Depends(tenant)):
    result = store.get(tenant_id, job_id)
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return result


@app.get("/v1/jobs/{job_id}/snapshots/{digest}", response_class=PlainTextResponse)
def get_snapshot(job_id: str, digest: str, tenant_id: str = Depends(tenant)):
    source = store.snapshot(tenant_id, job_id, digest)
    if source is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return source


@app.get("/v1/jobs/{job_id}/snapshots/{digest}/raw")
def get_raw_snapshot(job_id: str, digest: str, tenant_id: str = Depends(tenant)):
    record = store.snapshot_record(tenant_id, job_id, digest)
    if record is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return Response(content=record["raw"] or record["html"].encode("utf-8"),
                    media_type="application/octet-stream",
                    headers={"Content-Disposition": 'attachment; filename="capture.bin"',
                             "X-Content-Type-Options": "nosniff"})


@app.get("/review", response_class=HTMLResponse)
def review_app():
    """Serve the local workbench with a per-response nonce and no remote assets."""
    nonce = secrets.token_urlsafe(18)
    document = Path(__file__).with_name("workbench.html").read_text(encoding="utf-8").replace("__NONCE__", nonce)
    return HTMLResponse(document, headers={"Content-Security-Policy":
        f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'; frame-ancestors 'none'",
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff"})


@app.get("/v1/jobs/{job_id}/review", response_class=HTMLResponse)
def review(job_id: str, tenant_id: str = Depends(tenant)):
    result = store.get(tenant_id, job_id)
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    rows = []
    for name, field in result["fields"].items():
        evidence = field["evidence"] or [c["evidence"] for c in field["candidates"]]
        bits = []
        for item in evidence:
            capture = store.snapshot(tenant_id, job_id, item["snapshot_hash"])
            if capture is None:
                continue
            bits.append(f"<details><summary>{html.escape(item['source_url'])} · {html.escape(item['locator'])}</summary>"
                        f"<p>Fetched: {html.escape(item['fetched_at'])}; SHA-256: {html.escape(item['snapshot_hash'])}</p>"
                        f"<blockquote>{html.escape(item['excerpt'])}</blockquote><pre>{html.escape(capture)}</pre></details>")
        rows.append(f"<section><h2>{html.escape(name)}: {html.escape(field['state'])}</h2>"
                    f"<p>Value: {html.escape(str(field['value']))}</p>{''.join(bits)}</section>")
    document = "<!doctype html><html><head><meta charset='utf-8'><title>Evidence review</title></head><body><h1>Evidence review</h1>" + "".join(rows) + "</body></html>"
    return HTMLResponse(document, headers={"Content-Security-Policy": "default-src 'none'; base-uri 'none'",
                                           "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@app.delete("/v1/jobs/{job_id}")
def delete_job(job_id: str, tenant_id: str = Depends(tenant)):
    if not store.delete(tenant_id, job_id):
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return {"deleted": True}


@app.get("/v1/metrics")
def metrics(tenant_id: str = Depends(tenant)):
    return store.metrics(tenant_id)


@app.put("/v1/jobs/{job_id}/reviews/{field_name}")
def put_review(job_id: str, field_name: str, review: ReviewInput, tenant_id: str = Depends(tenant)):
    result = store.get(tenant_id, job_id)
    if result is None or field_name not in result["fields"]:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    try:
        return store.save_review(tenant_id, job_id, field_name, review.model_dump())
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "REVIEW_SESSION_REQUIRED", "message": str(exc)}) from exc


@app.post("/v1/jobs/{job_id}/reviews/{field_name}/start")
def start_review(job_id: str, field_name: str, session: ReviewSessionInput, tenant_id: str = Depends(tenant)):
    result = store.get(tenant_id, job_id)
    if result is None or field_name not in result["fields"]:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    try:
        return store.start_review(tenant_id, job_id, field_name, session.reviewer_id)
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "REVIEW_ALREADY_ACTIVE", "message": str(exc)}) from exc


@app.post("/v1/jobs/{job_id}/reviews/{field_name}/stop")
def stop_review(job_id: str, field_name: str, session: ReviewSessionInput, tenant_id: str = Depends(tenant)):
    result = store.get(tenant_id, job_id)
    if result is None or field_name not in result["fields"]:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    try:
        return store.stop_review(tenant_id, job_id, field_name, session.reviewer_id)
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "NO_ACTIVE_REVIEW", "message": str(exc)}) from exc


@app.get("/v1/jobs/{job_id}/reviews")
def get_reviews(job_id: str, tenant_id: str = Depends(tenant)):
    if store.get(tenant_id, job_id) is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return store.reviews(tenant_id, job_id)


@app.get("/v1/jobs/{job_id}/review-history")
def get_review_history(job_id: str, tenant_id: str = Depends(tenant)):
    if store.get(tenant_id, job_id) is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return {"sessions": store.review_sessions(tenant_id, job_id),
            "edits": store.review_history(tenant_id, job_id)}


@app.get("/v1/jobs/{job_id}/labels")
def export_labels(job_id: str, tenant_id: str = Depends(tenant)):
    result = store.get(tenant_id, job_id)
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    reviews = store.reviews(tenant_id, job_id)
    accepted = sum(result["fields"][name]["state"] == "verified" and item["verdict"] == "correct"
                   for name, item in reviews.items())
    sessions = store.review_sessions(tenant_id, job_id)
    minutes = sum(item["elapsed_seconds"] or 0 for item in sessions if item["stopped_at"] is not None) / 60
    return {"label_schema_version": "1.1", "job_id": job_id, "requested_url": result["requested_url"],
            "machine_schema_version": result["schema_version"], "extraction_version": result["extraction_version"],
            "request": store.get_request(tenant_id, job_id),
            "machine_result": result, "reviews": reviews, "reviewed_fields": len(reviews),
            "review_sessions": sessions, "review_history": store.review_history(tenant_id, job_id),
            "review_minutes_total": round(minutes, 4), "review_minutes_per_accepted_field":
                round(minutes / accepted, 4) if accepted else None,
            "capture_hashes": [page["snapshot_hash"] for page in result["pages"] if "snapshot_hash" in page]}


@app.get("/v1/jobs/{job_id}/review-evidence")
def review_evidence(job_id: str, tenant_id: str = Depends(tenant)):
    result = store.get(tenant_id, job_id)
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    output = {}
    for name, field in result["fields"].items():
        evidence = field["evidence"] + [candidate["evidence"] for candidate in field["candidates"]]
        rows = []
        for item in evidence:
            bound = next((page for page in result["pages"] if page.get("url") == item["source_url"] and
                          page.get("fetched_at") == item["fetched_at"] and
                          page.get("snapshot_hash") == item["snapshot_hash"]), None)
            capture = store.snapshot_record(tenant_id, job_id, item["snapshot_hash"]) if bound else None
            raw = capture["raw"] or capture["html"].encode("utf-8") if capture else None
            decoded = raw.decode(bound.get("encoding", "utf-8"), "replace") if raw is not None else None
            rows.append({**item, "source_node": source_node_for(decoded, item["locator"]) if decoded else None,
                         "source_bound": bool(bound and raw is not None), "capture_text": decoded})
        output[name] = rows
    return output

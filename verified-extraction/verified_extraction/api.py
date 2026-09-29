from __future__ import annotations

import hmac
import html
import logging
import os
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.responses import HTMLResponse, PlainTextResponse

from .models import JobRequest
from .security import FetchError
from .service import run_job, METRICS
from .store import Store

logging.basicConfig(level=logging.INFO, format='%(message)s')
app = FastAPI(title="Verified Extraction", version="0.1.0")
store = Store(os.environ.get("VE_DB", "data/verified_extraction.sqlite3"))


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
    existing = store.by_key(tenant_id, request.idempotency_key)
    if existing:
        if existing[0] != fingerprint:
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
        return existing[1]
    try:
        result, snapshots = run_job(request)
    except FetchError as exc:
        raise HTTPException(422, detail={"code": exc.code, "message": str(exc)})
    output = result.model_dump()
    store.put(tenant_id, result.job_id, request.idempotency_key, fingerprint, output, snapshots)
    return output


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
    return "<!doctype html><html><head><meta charset='utf-8'><title>Evidence review</title><style>body{font:16px system-ui;max-width:900px;margin:2rem auto;padding:1rem}pre{white-space:pre-wrap;max-height:24rem;overflow:auto;background:#eee;padding:1rem}section{border-top:1px solid #aaa}</style></head><body><h1>Evidence review</h1>" + "".join(rows) + "</body></html>"


@app.delete("/v1/jobs/{job_id}")
def delete_job(job_id: str, tenant_id: str = Depends(tenant)):
    if not store.delete(tenant_id, job_id):
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return {"deleted": True}


@app.get("/v1/metrics")
def metrics(tenant_id: str = Depends(tenant)):
    return dict(METRICS)

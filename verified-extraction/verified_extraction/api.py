from __future__ import annotations

import hmac
import html
import logging
import os
import secrets
import time
from fastapi import FastAPI, Header, HTTPException, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.responses import JSONResponse

from .models import JobRequest
from .security import FetchError
from .service import run_job
from .store import Store

logging.basicConfig(level=logging.INFO, format='%(message)s')
app = FastAPI(title="Verified Extraction", version="0.1.0")
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
    wait_until = time.monotonic() + request.options.deadline_seconds + 10
    while True:
        state = store.claim(tenant_id, request.idempotency_key, fingerprint)
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
        result, snapshots = run_job(request)
        output = result.model_dump()
        store.put(tenant_id, result.job_id, request.idempotency_key, fingerprint, output, snapshots)
        return output
    except FetchError as exc:
        store.release_claim(tenant_id, request.idempotency_key, fingerprint)
        raise HTTPException(422, detail={"code": exc.code, "message": str(exc)})
    except Exception:
        store.release_claim(tenant_id, request.idempotency_key, fingerprint)
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
    """Token stays in page memory; all customer content enters through textContent."""
    nonce = secrets.token_urlsafe(18)
    document = """<!doctype html><html><head><meta charset='utf-8'><title>Evidence review</title></head>
<body><h1>Evidence review</h1><form id='form'><label>Job ID <input id='job' required></label>
<label>Bearer key <input id='key' type='password' required autocomplete='off'></label>
<button>Load</button></form><div id='result'></div>
<script nonce='NONCE'>
const form=document.getElementById('form'), out=document.getElementById('result');
function add(parent,tag,value){const node=document.createElement(tag);node.textContent=value;parent.append(node);return node;}
form.addEventListener('submit',async event=>{event.preventDefault();out.replaceChildren();
 const id=document.getElementById('job').value.trim(), key=document.getElementById('key').value;
 if(!/^[0-9a-f-]{36}$/i.test(id)){add(out,'p','Invalid job ID');return;}
 const headers={Authorization:'Bearer '+key};
 const response=await fetch('/v1/jobs/'+id,{headers,cache:'no-store'});
 if(!response.ok){add(out,'p','Could not load job ('+response.status+')');return;}
 const job=await response.json();
 for(const [name,field] of Object.entries(job.fields)){
  const section=add(out,'section','');add(section,'h2',name+' — '+field.state);
  add(section,'p','Value: '+JSON.stringify(field.value)+(field.unit?' / '+field.unit:'')+(field.currency?' '+field.currency:''));
  const evidence=[...field.evidence,...field.candidates.map(c=>c.evidence)];
  for(const item of evidence){add(section,'h3',item.source_url+' '+item.locator);add(section,'blockquote',item.excerpt);
   const capture=await fetch('/v1/jobs/'+id+'/snapshots/'+item.snapshot_hash,{headers,cache:'no-store'});
   add(section,'pre',capture.ok?await capture.text():'Capture unavailable');}
 }
});
</script></body></html>""".replace("NONCE", nonce)
    return HTMLResponse(document, headers={"Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'; base-uri 'none'; form-action 'none'",
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

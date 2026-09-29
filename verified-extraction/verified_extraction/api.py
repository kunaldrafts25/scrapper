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

from .models import JobRequest, ReviewInput, ReviewSessionInput
from .security import FetchError
from .worker import run_hard
from .extract import source_node_for
from .store import Store

logging.basicConfig(level=logging.INFO, format='%(message)s')
app = FastAPI(title="Verified Extraction", version="0.4.0")
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
        raise HTTPException(status, detail={"code": exc.code, "message": str(exc)})
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
    """Token stays in page memory; all customer content enters through textContent."""
    nonce = secrets.token_urlsafe(18)
    document = """<!doctype html><html><head><meta charset='utf-8'><title>Evidence review</title></head>
<body><h1>Evidence review</h1><form id='form'><label>Job ID <input id='job' required></label>
<label>Bearer key <input id='key' type='password' required autocomplete='off'></label>
<label>Reviewer ID <input id='reviewer' required></label>
<button>Load</button></form><button id='export' type='button' disabled>Download labels</button><div id='result'></div>
<script nonce='NONCE'>
const form=document.getElementById('form'), out=document.getElementById('result'), exportButton=document.getElementById('export');
let activeId='', activeHeaders=null, activeReviewer='', activeField='';
function add(parent,tag,value){const node=document.createElement(tag);node.textContent=value;parent.append(node);return node;}
form.addEventListener('submit',async event=>{event.preventDefault();out.replaceChildren();
 const id=document.getElementById('job').value.trim(), key=document.getElementById('key').value;
 activeReviewer=document.getElementById('reviewer').value.trim();
 if(!/^[0-9a-f-]{36}$/i.test(id)){add(out,'p','Invalid job ID');return;}
 const headers={Authorization:'Bearer '+key};
 const response=await fetch('/v1/jobs/'+id,{headers,cache:'no-store'});
 if(!response.ok){add(out,'p','Could not load job ('+response.status+')');return;}
 const job=await response.json();
 activeId=id;activeHeaders=headers;exportButton.disabled=false;
 const reviewResponse=await fetch('/v1/jobs/'+id+'/reviews',{headers,cache:'no-store'});
 const saved=reviewResponse.ok?await reviewResponse.json():{};
 const historyResponse=await fetch('/v1/jobs/'+id+'/review-history',{headers,cache:'no-store'});
 const history=historyResponse.ok?await historyResponse.json():{sessions:[]};
 activeField=history.sessions.find(item=>item.stopped_at===null && item.reviewer_id===activeReviewer)?.field_name||'';
 const evidenceResponse=await fetch('/v1/jobs/'+id+'/review-evidence',{headers,cache:'no-store'});
 const evidenceMap=evidenceResponse.ok?await evidenceResponse.json():{};
 for(const [name,field] of Object.entries(job.fields)){
  const section=add(out,'section','');add(section,'h2',name+' — '+field.state);
  add(section,'p','Value: '+JSON.stringify(field.value)+(field.unit?' / '+field.unit:'')+(field.currency?' '+field.currency:''));
  const evidence=evidenceMap[name]||[];
  for(const item of evidence){add(section,'h3',item.source_url+' '+item.locator);add(section,'blockquote',item.excerpt);
   if(item.source_node){add(section,'p','Source node:');add(section,'mark',item.source_node);}
   if(!item.source_bound){add(section,'p','Source binding could not be checked');continue;}
   const pre=add(section,'pre','');
   const raw=item.capture_text||'', position=item.source_node?raw.indexOf(item.source_node):-1;
   if(position>=0){pre.append(document.createTextNode(raw.slice(0,position)));
    add(pre,'mark',item.source_node);pre.append(document.createTextNode(raw.slice(position+item.source_node.length)));}
   else pre.textContent=raw;}
  const verdict=section.appendChild(document.createElement('select'));
  for(const choice of ['','correct','wrong','unsupported','conflicting','uncertain']){
   const option=document.createElement('option');option.value=choice;option.textContent=choice||'Choose verdict';verdict.append(option);}
  verdict.value=saved[name]?.verdict||'';
  const corrected=section.appendChild(document.createElement('input'));corrected.placeholder='Corrected value (optional)';corrected.value=saved[name]?.corrected_value||'';
  const source=section.appendChild(document.createElement('input'));source.placeholder='Corrected source URL (optional)';source.value=saved[name]?.corrected_source_url||'';
  const excerpt=section.appendChild(document.createElement('input'));excerpt.placeholder='Supporting excerpt (optional)';excerpt.value=saved[name]?.corrected_excerpt||'';
  const reason=section.appendChild(document.createElement('textarea'));reason.placeholder='Short reason';reason.value=saved[name]?.reason||'';
  const timer=add(section,'p','Recorded seconds: '+(saved[name]?.time_spent_seconds||0));
  const startButton=add(section,'button','Start review'), stopButton=add(section,'button','Stop review');
  startButton.type='button';stopButton.type='button';
  const save=add(section,'button','Save verdict'), status=add(section,'p',activeField===name?'Review session is active':'');save.type='button';
  startButton.addEventListener('click',async()=>{
   if(activeField){status.textContent='Stop the active field first';return;}
   const response=await fetch('/v1/jobs/'+id+'/reviews/'+encodeURIComponent(name)+'/start',
    {method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({reviewer_id:activeReviewer})});
   if(response.ok){activeField=name;status.textContent='Reviewing '+name;}else status.textContent='Start failed ('+response.status+')';
  });
  stopButton.addEventListener('click',async()=>{
   if(activeField!==name){status.textContent='This field is not active';return;}
   const response=await fetch('/v1/jobs/'+id+'/reviews/'+encodeURIComponent(name)+'/stop',
    {method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({reviewer_id:activeReviewer})});
   if(response.ok){activeField='';const data=await response.json();status.textContent='Stopped; session '+data.elapsed_seconds+' seconds';}
   else status.textContent='Stop failed ('+response.status+')';
  });
  save.addEventListener('click',async()=>{
   if(!verdict.value){status.textContent='Choose a verdict';return;}
   const body={verdict:verdict.value,corrected_value:corrected.value||null,corrected_source_url:source.value||null,
    corrected_excerpt:excerpt.value||null,reason:reason.value,reviewer_id:activeReviewer};
   const response=await fetch('/v1/jobs/'+id+'/reviews/'+encodeURIComponent(name),{method:'PUT',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(body)});
   status.textContent=response.ok?'Saved':'Save failed ('+response.status+')';
   if(response.ok){const data=await response.json();timer.textContent='Recorded seconds: '+data.time_spent_seconds;}
  });
 }
});
exportButton.addEventListener('click',async()=>{
 if(!activeId||!activeHeaders)return;
 const response=await fetch('/v1/jobs/'+activeId+'/labels',{headers:activeHeaders,cache:'no-store'});
 if(!response.ok){add(out,'p','Export failed ('+response.status+')');return;}
 const data=await response.json(), blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
 const link=document.createElement('a'), objectUrl=URL.createObjectURL(blob);
 link.href=objectUrl;link.download='verified-extraction-labels.json';link.click();setTimeout(()=>URL.revokeObjectURL(objectUrl),1000);
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

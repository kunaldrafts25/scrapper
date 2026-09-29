from __future__ import annotations

import logging
import json
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .extract import extract_fields, links_for, snapshot_hash
from .fetch import HTTPFetcher, Page
from .models import JobRequest, Result
from .security import FetchError, canonical_url, tracking_key

log = logging.getLogger("verified_extraction")
METRICS = {"jobs": 0, "fetch_failures": 0, "empty_pages": 0, "missing_fields": 0,
           "blocked_fields": 0, "unverified_fields": 0, "conflicting_fields": 0,
           "verified_fields": 0, "model_tokens": 0, "elapsed_seconds_total": 0.0,
           "estimated_internal_cost_usd_total": 0.0, "evidence_check_failures": 0,
           "unsupported_fields": 0}


def run_job(request: JobRequest, fetcher=None) -> tuple[Result, dict[str, str]]:
    start = time.monotonic()
    seed = canonical_url(request.url)
    host = urlsplit(seed).hostname
    allowed = {host}
    for name in request.allowed_hostnames:
        parsed = canonical_url("https://" + name + "/")
        allowed.add(urlsplit(parsed).hostname)
    deadline = start + request.options.deadline_seconds
    fetcher = fetcher or HTTPFetcher(allowed, deadline)
    queue = [(seed, 0)]
    for hint in request.page_hints if request.options.max_depth >= 1 else []:
        try:
            from urllib.parse import urljoin
            hinted = canonical_url(urljoin(seed, hint))
            if urlsplit(hinted).hostname in allowed:
                queue.append((hinted, 1))
        except FetchError:
            pass
    seen = set()
    seen_captures = set()
    pages: list[Page] = []
    page_rows = []
    errors = []
    blocked = False
    pages_fetched = 0
    while queue and len(seen) < request.options.max_pages:
        url, depth = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        if time.monotonic() >= deadline:
            errors.append({"code": "DEADLINE", "url": url, "message": "Job deadline reached"})
            break
        try:
            page = fetcher.fetch(url)
            pages_fetched += 1
            capture_key = (tracking_key(page.url), snapshot_hash(page.html))
            duplicate = capture_key in seen_captures
            seen_captures.add(capture_key)
            if not duplicate:
                pages.append(page)
            page_rows.append({"url": page.url, "requested_url": url, "redirects": page.redirects,
                              "fetched_at": page.fetched_at, "snapshot_hash": snapshot_hash(page.html),
                              "status": "duplicate" if duplicate else "fetched"})
            if not duplicate and depth < request.options.max_depth:
                queue.extend((link, depth + 1) for link in links_for(page, allowed, request.page_hints) if link not in seen)
            if not page.html.strip():
                METRICS["empty_pages"] += 1
                log.warning(json.dumps({"event": "empty_page"}))
        except FetchError as exc:
            errors.append({"code": exc.code, "url": url, "message": str(exc)})
            page_rows.append({"requested_url": url, "status": "error", "error_code": exc.code})
            METRICS["fetch_failures"] += 1
            log.warning(json.dumps({"event": "fetch_error", "code": exc.code}))
            if exc.code in {"ACCESS_DENIED", "ROBOTS_DENIED", "ROBOTS_UNAVAILABLE", "PRIVATE_TARGET"}:
                blocked = True
                if exc.code == "ACCESS_DENIED":
                    break
    fields = extract_fields(pages, request.schema_["properties"], blocked)
    for field in fields.values():
        METRICS[field.state + "_fields"] += 1
        if field.state == "unverified":
            METRICS["evidence_check_failures"] += 1
    states = {field.state for field in fields.values()}
    status = "complete" if states == {"verified"} and not errors else "failed" if not pages else "partial"
    elapsed = round(time.monotonic() - start, 4)
    METRICS["jobs"] += 1
    METRICS["elapsed_seconds_total"] += elapsed
    usage = {"pages_fetched": pages_fetched, "browser_renders": 0, "model_tokens": 0,
             "elapsed_seconds": elapsed, "estimated_internal_cost_usd": 0.0,
             "cost_note": "HTTP and local compute not metered in MVP"}
    result = Result(job_id=str(uuid.uuid4()), status=status, requested_url=seed,
                    observed_at=datetime.now(timezone.utc).isoformat(), fields=fields,
                    pages=page_rows, errors=errors, usage=usage)
    log.info(json.dumps({"event": "job_complete", "status": status, "pages": len(pages),
                         "elapsed_seconds": elapsed, "field_states": sorted(states)}))
    return result, {snapshot_hash(page.html): page.html for page in pages}

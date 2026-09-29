# Verified Extraction MVP

One-shot extraction of **explicitly labeled scalar facts** from public HTML pages. The service retains the original response bytes and returns a value as `verified` only when its label, value, DOM location and source hash can be checked against that capture. It abstains on unsupported fields and reports conflicts. This is a local research MVP, not a production security boundary or a claim of fact-level truth: a page can itself be wrong, stale, or adversarial.

## Local setup (PowerShell)

```powershell
cd D:\scrapper\verified-extraction
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
$env:VE_KEYS='{"local":"replace-with-a-long-random-local-secret"}'
.\.venv\Scripts\python.exe -m uvicorn verified_extraction.api:app --host 127.0.0.1 --port 8000
```

In a second PowerShell window, set `$env:VE_KEYS` only if starting another server; use the same bearer key in the request:

```powershell
$body = @{ url='https://example.com/'; schema=@{ type='object'; properties=@{
  title=@{type='string'}; support=@{type='string'}; plan_price=@{type='number';'x-unit'='month';'x-currency'='USD'}
} }; options=@{max_pages=2;max_depth=1;deadline_seconds=15}; idempotency_key='example-1' } | ConvertTo-Json -Depth 8
Invoke-RestMethod -Uri http://127.0.0.1:8000/v1/jobs -Method Post -ContentType application/json -Headers @{Authorization='Bearer replace-with-a-long-random-local-secret'} -Body $body
```

The example.com page is public but is unlikely to have these labels, so its fields should be `missing`. A representative field response for a page containing `<p>Support: Email</p>` is:

```json
{"schema_version":"1.2","extraction_version":"0.4.0","job_id":"<uuid>","status":"partial","requested_url":"https://example.com/","observed_at":"<timestamp>","fields":{"support":{"state":"verified","value":"Email","unit":null,"currency":null,"numeric_encoding":null,"evidence":[{"source_url":"https://example.com/","fetched_at":"<timestamp>","excerpt":"Support: Email","locator":"p:nth-of-type(1)","snapshot_hash":"<sha256-of-response-bytes>","label":"Support","raw_value":"Email"}],"candidates":[],"reason":null}},"pages":[{"url":"https://example.com/","requested_url":"https://example.com/","fetched_at":"<timestamp>","status":"fetched","snapshot_hash":"<sha256>","encoding":"utf-8","decoding_errors":0,"content_type":"text/html"}],"errors":[],"usage":{"pages_fetched":1,"http_requests_started":2,"browser_renders":0,"model_tokens":0,"elapsed_seconds":0.01,"estimated_internal_cost_usd":0.0,"cost_note":"HTTP and local compute not metered in MVP"}}
```

Actual responses include all 3–10 requested fields and full page metadata. Open `http://127.0.0.1:8000/review` and enter the job ID, bearer key and reviewer ID to inspect evidence. The key remains in page memory and is sent only in the `Authorization` header; it is not placed in the URL or stored by the page. Review one field at a time using **Start review**, **Stop review**, then **Save verdict**. The server records nonoverlapping elapsed sessions and preserves each later edit. Authenticated API clients use `POST /v1/jobs/{job_id}/reviews/{field_name}/start` and `/stop` with `{"reviewer_id":"analyst-1"}` before `PUT /v1/jobs/{job_id}/reviews/{field_name}` with verdict, reason and reviewer ID. `PUT` rejects client-supplied `time_spent_seconds`; it uses completed server sessions. `GET /v1/jobs/{job_id}/review-history` returns all sessions and edits. Other routes: `GET /v1/jobs/{job_id}`, `/review-evidence`, `/reviews`, `/labels`, `/snapshots/{snapshot_hash}` and `/snapshots/{snapshot_hash}/raw`. `DELETE /v1/jobs/{job_id}` deletes the result, reviews, sessions and captures.

## Input and behavior

- JSON Schema subset: root object with 3–10 scalar properties (`string`, `number`, `integer`, `boolean`). Supported root keys are `type`, `properties`, `required`, `title`, and `description`; supported property keys are `type`, `title`, `description`, `x-unit`, and `x-currency`. Unknown keywords and malformed values return structured HTTP 422 `INVALID_REQUEST`. A property's `title` is its exact label; otherwise the property name with underscores replaced by spaces is used. Visible `Label: value` or `Label - value` text, table rows, and matching top-level JSON-LD keys are supported. No general semantic extraction is claimed.
- Numeric `x-unit` can request `month`, `year`, `day`, `user`, `seat`, or `GB`; `x-currency` can request `USD`, `EUR`, `GBP`, or `INR`. Source units and currency are always retained in the field or conflict candidates. `$29/month` and `$29/year` are distinct claims even when the numeric values match. An unsupported unit or a mismatch to the requested unit yields `unverified` when no supported candidate remains. No currency conversion is performed.
- **Exact numeric JSON policy (schema version 1.1):** integers within JavaScript's exact JSON integer range (±9,007,199,254,740,991) are JSON numbers. Larger integers are decimal strings with `numeric_encoding: "integer-string"`. Non-integral decimal values are canonical decimal strings with `numeric_encoding: "decimal-string"`; no float conversion is used. The exact source spelling remains in `evidence.raw_value`. JSON-LD decimal values use exact decimal parsing. This is an intentional change from schema version 1.0.
- Optional `allowed_hostnames` can add exact hosts; `page_hints` can add likely pages. Crawl limits: 1–8 pages, depth 0–2, deadline 2–45 seconds, and `max_http_requests` 1–50 including robots and redirects. Defaults: 3 pages, depth 1, 20 seconds, 20 total HTTP requests. The request ceiling applies to one attempt; an approved evaluation must budget possible retries separately.
- Synchronous `POST /v1/jobs` starts one killable child process for the crawl and extraction. The parent enforces the wall-clock deadline and returns HTTP 504 `DEADLINE` if it expires. SQLite atomically claims each tenant/key; the parent renews a four-second lease while waiting. Concurrent identical requests wait for one result; a different payload returns HTTP 409 `IDEMPOTENCY_CONFLICT`. After a parent crash, the same payload can reclaim an expired lease. A stale owner cannot commit a result. If a reclaim races with an orphaned worker, extra fetch work may occur, but only one result can be stored for the key.
- `verified` means the candidate is supported by the retained capture, not that the website's claim is true. Different supported values become `conflicting`, with both candidates. Missing and blocked fields have reasons. Successful HTTP or JSON parsing never sets verification state by itself.
- Only public HTTP(S) destinations are fetched. Each connection pins a freshly checked global IP, redirects and robots paths are checked, browser rendering and asset fetching are disabled. The user agent identifies this MVP. Page responses are capped at 1 MB and redirects at four. SQLite holds a cross-process, renewable host lease from DNS through response close, including robots and redirects. It enforces at least 0.5 seconds between actual request starts and any longer robots crawl delay. A dead worker's host lease expires after four seconds. An access denial stops the crawl. No proxy, CAPTCHA, authentication or paywall bypass is present.
- Tracking-parameter variants keep their original fetch URLs. Identical bytes can share one stored capture, while **every fetched URL and timestamp** remains in the page log and field evidence; every fetch counts toward the budget. Evidence verification checks its URL and timestamp against the corresponding page event, in addition to the byte hash and content.
- `max_depth=0` fetches only the seed; page hints start at depth 1. `robots.txt` status 200 is parsed, 404 means no rules, and every other status fails closed. Each redirect destination is checked before fetching.
- The snapshot SHA-256 is computed from original response bytes. Decoding preference is BOM, valid HTTP charset, valid HTML meta charset, then UTF-8. Invalid byte sequences are replaced with U+FFFD and counted in `pages[].decoding_errors`. The decoded capture is available for review, while `/raw` returns exactly the retained bytes. Byte-only calls to the evidence verifier raise `TypeError`; pass a `Page` carrying raw bytes, encoding, URL and fetch time. DOM locators use parser-reproducible `tag:nth-of-type(n)` paths; JSON-LD locators include script path, array item and key. Verification rechecks the label, raw value, exact parsed number, locator, byte hash and source binding together.
- `VE_KEYS` maps tenant IDs to bearer keys. Bind only to loopback locally. Keys are read from the environment and never logged. Result and snapshot rows have tenant-scoped keys; no cross-tenant result cache exists.
- Original bytes, decoded HTML, request settings, machine results, reviewer verdict history and sessions, and per-host leases reside in SQLite at `data/verified_extraction.sqlite3` unless `VE_DB` is set. The service removes jobs, reviews and captures older than seven days on subsequent API access. Deletion is immediate; SQLite free pages and backups may still contain prior bytes, so secure erasure is **not** guaranteed. Protect the DB file and backups.
- Logs report event, state, pages and latency without page text. Usage reports zero model and browser usage. Internal cost is a placeholder of 0 USD for this local run; HTTP and compute are not metered. There is no spend-cap enforcement because no paid providers are used.
- `GET /v1/metrics` returns persisted aggregate counters only for the authenticated tenant. No field content or other tenant's usage is returned. Internal process logs remain coarse.

## Offline verification and benchmark

```powershell
cd D:\scrapper\verified-extraction
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m benchmark.run --suite v2 --split all
.\.venv\Scripts\python.exe -m benchmark.run --suite v2 --split held_out
.\.venv\Scripts\python.exe -m benchmark.run --suite smoke --split all
.\.venv\Scripts\python.exe -m benchmark.measure_reliability
.\.venv\Scripts\python.exe -m benchmark.measure_host_policy
```

These commands use only frozen local HTML, no network or paid API. `benchmark/cases.json` retains the original six smoke cases. `benchmark/v2_cases.json` contains 16 versioned, synthetic cases across 16 distinct site names, split by site. They were authored for this repository and require no third-party capture permission. Categories cover static text, tables, JSON-LD, hidden content, sparse and malformed HTML, encodings, redirects, robots failures, prices with conflicting periods, page conflicts, unsupported units, multilingual text, JavaScript shells, and prompt injection. Robots errors and redirects in the benchmark are frozen fetch outcomes; the HTTP policy itself is tested separately with mocked responses.

The benchmark emits raw per-case expected and actual fields, evidence validity, latency and failures, plus category summaries and denominators. Accepted fields with invalid evidence count as incorrect. It reports precision, recall, abstention, conflict detection, evidence validity, latency and failure rates. Setup/review time and cost per accepted field remain null until measured. These synthetic fixtures are functional checks, not evidence of live-site accuracy, competitor performance or calibrated confidence.

For a proposed real-site evaluation, see [EVALUATION_PLAN.md](EVALUATION_PLAN.md). `benchmark/procurement_schema.json` fixes the six-field one-plan task. The 24-row `benchmark/candidate_targets.csv` fixes a proposed 12/12 split but contains unverified site, plan and access assumptions; `benchmark.validate_targets` rejects it until approvals and checks are recorded. No live crawl is authorized. Offline `benchmark.export_local --plan-name ...` freezes a previously approved job into raw bytes, settings, code revision and a blind template. A development site uses `labels.json`; a held-out site requires `labels.reviewer-a.json`, `labels.reviewer-b.json`, and `adjudication.json`. `manual_baseline.json` records separate analyst time and proposed values. After blind labels are sealed, save the machine-assisted `/labels` export as `assisted_reviews.json`. `benchmark.replay` checks literal source claims, preserves original labels, and reports blind-label time, manual-baseline time, and assisted-review time separately. No real-site or competitor result has been recorded.

## Scope and current gaps

JavaScript-only sites, nested values, synonyms, unlabeled values, pages requiring cookies, and PDFs are unsupported. A JS shell yields missing fields. Hidden detection handles the HTML `hidden` attribute, hidden ancestors, `aria-hidden=true`, and inline `display:none`, `visibility:hidden`, and `opacity:0`. External stylesheets, CSS classes, media queries, pseudo-elements, inherited computed styles beyond these inline rules, and script-driven visibility are unsupported; a page using them can still yield a false visible candidate. Browser subresource SSRF is avoided by having no browser or asset loader. The local API has static bearer keys, synchronous requests, one subprocess per job, no request-level rate limit, no TLS termination and no durable job queue. SQLite is not encrypted at rest. Hard termination depends on operating-system process scheduling; the measured overshoot is not a guaranteed maximum. Do not expose the service publicly without stronger authentication, quotas, controlled network egress, encrypted storage/backups and operational monitoring.

### Interrupted-job recovery

If a child process fails or times out while the API parent is alive, the parent kills it, releases the idempotency claim, and returns `WORKER_FAILED` (503) or `DEADLINE` (504). If the parent itself crashes, wait at least four seconds for the claim lease to expire, then retry the **same** tenant, key and payload. A different payload still returns 409 until the job/key is deleted or expires. A stale owner cannot store a result after another owner takes the lease. The retry may perform additional HTTP requests if an orphaned worker is still alive, so count this possibility in an approved request budget. `python -m benchmark.measure_reliability` measures local deadline overshoot and claim recovery without network access.

## Reference review and reuse

Read-only references inspected: `D:\Ryze-MCP-Enterprise\Ryze-MCP\ryze_tools\Research\brand_scraper.py`, its regular and adversarial tests, `cache\ai_cache.py`, `config.py`, `audits\mcp_repo_audit.md`, `audits\crawl4ai_capabilities.md`, `audits\firecrawl_free_tier.md`; and `D:\Ryze-MCP_Latest\Ryze-MCP_Latest-main\Ryze-MCP\ryze_tools\Research\_brand_cache.py` plus its MIT license. The Enterprise frontend's `AGENTS.md` does not apply to this separate project. No code was copied. Ideas reused conceptually: bounded crawl, robots handling, static HTML first, and adversarial fixture coverage. Fetching, evidence verification, API, storage and benchmark were rewritten. The Ryze domain/depth cache scheme is intentionally absent because it cannot isolate tenants or schemas.

## Fair competitor test proposal

Freeze a consented set of public sites and independent labeled evidence, with separate development and held-out groups. Give every adapter the same seed, schema, page and time limits; record all provider calls, costs, returned sources and manual review minutes. Score only values that the independent reviewer can support from frozen captures; report abstentions, conflicts, error rates and confidence intervals alongside precision, recall, latency and cost per accepted field. `BenchmarkAdapter` provides the interface. Ryze, Firecrawl, Zyte, Diffbot and crawler-plus-model adapters are deliberately unconfigured; no competitor result is claimed. Before a live comparison, obtain the user's approval for the exact target list, request volume, accounts, estimated charges and impact, then configure provider credentials and honor each site's access rules.

For a paid pilot: add a controlled egress proxy/firewall, external authentication and TLS, a job queue, encrypted tenant-isolated storage, retention enforcement with backups, rate and cost limits, auditability, operational metrics, broader labeled evaluation, and a reviewed acceptable-use process. Reassess the commercial hypothesis using the held-out benchmark before expanding the platform.

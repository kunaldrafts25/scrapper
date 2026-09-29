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
{"schema_version":"1.0","extraction_version":"0.2.0","job_id":"<uuid>","status":"partial","requested_url":"https://example.com/","observed_at":"<timestamp>","fields":{"support":{"state":"verified","value":"Email","unit":null,"currency":null,"evidence":[{"source_url":"https://example.com/","fetched_at":"<timestamp>","excerpt":"Support: Email","locator":"p:nth-of-type(1)","snapshot_hash":"<sha256-of-response-bytes>","label":"Support","raw_value":"Email"}],"candidates":[],"reason":null}},"pages":[{"status":"fetched","snapshot_hash":"<sha256>","encoding":"utf-8","decoding_errors":0}],"errors":[],"usage":{"pages_fetched":1,"browser_renders":0,"model_tokens":0,"elapsed_seconds":0.01,"estimated_internal_cost_usd":0.0,"cost_note":"HTTP and local compute not metered in MVP"}}
```

Actual responses include all 3–10 requested fields and full page metadata. Open `http://127.0.0.1:8000/review` and enter the job ID and bearer key to inspect evidence. The key remains in page memory and is sent only in the `Authorization` header; it is not placed in the URL or stored by the page. Authenticated API clients can also use `GET /v1/jobs/{job_id}`, `GET /v1/jobs/{job_id}/review`, `GET /v1/jobs/{job_id}/snapshots/{snapshot_hash}` for decoded text, and `GET /v1/jobs/{job_id}/snapshots/{snapshot_hash}/raw` for the original bytes. `DELETE /v1/jobs/{job_id}` deletes the result and captures.

## Input and behavior

- JSON Schema subset: root object with 3–10 scalar properties (`string`, `number`, `integer`, `boolean`). Supported root keys are `type`, `properties`, `required`, `title`, and `description`; supported property keys are `type`, `title`, `description`, `x-unit`, and `x-currency`. Unknown keywords and malformed values return structured HTTP 422 `INVALID_REQUEST`. A property's `title` is its exact label; otherwise the property name with underscores replaced by spaces is used. Visible `Label: value` or `Label - value` text, table rows, and matching top-level JSON-LD keys are supported. No general semantic extraction is claimed.
- Numeric `x-unit` can request `month`, `year`, `day`, `user`, `seat`, or `GB`; `x-currency` can request `USD`, `EUR`, `GBP`, or `INR`. Source units and currency are always retained in the field or conflict candidates. `$29/month` and `$29/year` are distinct claims even when the numeric values match. An unsupported unit or a mismatch to the requested unit yields `unverified` when no supported candidate remains. No currency conversion is performed.
- Optional `allowed_hostnames` can add exact hosts; `page_hints` can add likely pages. Crawl limits: 1–8 pages, depth 0–2, deadline 2–45 seconds. Default 3 pages, depth 1, 20 seconds.
- Synchronous `POST /v1/jobs` performs one bounded crawl. SQLite atomically claims each tenant/key before crawling, including across workers sharing one DB. Concurrent identical requests wait for the same result; a different payload returns HTTP 409 `IDEMPOTENCY_CONFLICT`. A crashed worker can leave a pending claim that returns 503 `JOB_IN_PROGRESS` until operational recovery or retention expiry.
- `verified` means the candidate is supported by the retained capture, not that the website's claim is true. Different supported values become `conflicting`, with both candidates. Missing and blocked fields have reasons. Successful HTTP or JSON parsing never sets verification state by itself.
- Only public HTTP(S) destinations are fetched. Each connection pins a freshly checked global IP, redirects and robots paths are checked, browser rendering and asset fetching are disabled. The user agent identifies this MVP. Page responses are capped at 1 MB, redirects at four, host pacing at 0.5 seconds, and an access denial stops the crawl. No proxy, CAPTCHA, authentication or paywall bypass is present.
- Tracking-parameter variants keep their original fetch URLs; only captures with the same normalized tracking key **and** identical HTML hash are deduplicated for extraction. Every fetch still counts toward the page budget.
- `max_depth=0` fetches only the seed; page hints start at depth 1. `robots.txt` status 200 is parsed, 404 means no rules, and every other status fails closed. Each redirect destination is checked before fetching.
- The snapshot SHA-256 is computed from original response bytes. Decoding preference is BOM, valid HTTP charset, valid HTML meta charset, then UTF-8. Invalid byte sequences are replaced with U+FFFD and counted in `pages[].decoding_errors`. The decoded capture is available for review, while `/raw` returns exactly the retained bytes. DOM locators use parser-reproducible `tag:nth-of-type(n)` paths; JSON-LD locators include script path, array item and key. Verification rechecks the label, raw value, parsed value/unit/currency, locator and byte hash together.
- `VE_KEYS` maps tenant IDs to bearer keys. Bind only to loopback locally. Keys are read from the environment and never logged. Result and snapshot rows have tenant-scoped keys; no cross-tenant result cache exists.
- Original bytes, decoded HTML, and results reside in SQLite at `data/verified_extraction.sqlite3` unless `VE_DB` is set. The service removes jobs and captures older than seven days on subsequent API access. Deletion is immediate; SQLite free pages and backups may still contain prior bytes, so secure erasure is **not** guaranteed. Protect the DB file and backups.
- Logs report event, state, pages and latency without page text. Usage reports zero model and browser usage. Internal cost is a placeholder of 0 USD for this local run; HTTP and compute are not metered. There is no spend-cap enforcement because no paid providers are used.
- `GET /v1/metrics` returns persisted aggregate counters only for the authenticated tenant. No field content or other tenant's usage is returned. Internal process logs remain coarse.

## Offline verification and benchmark

```powershell
cd D:\scrapper\verified-extraction
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m benchmark.run --suite v2 --split all
.\.venv\Scripts\python.exe -m benchmark.run --suite v2 --split held_out
.\.venv\Scripts\python.exe -m benchmark.run --suite smoke --split all
```

These commands use only frozen local HTML, no network or paid API. `benchmark/cases.json` retains the original six smoke cases. `benchmark/v2_cases.json` contains 16 versioned, synthetic cases across 16 distinct site names, split by site. They were authored for this repository and require no third-party capture permission. Categories cover static text, tables, JSON-LD, hidden content, sparse and malformed HTML, encodings, redirects, robots failures, prices with conflicting periods, page conflicts, unsupported units, multilingual text, JavaScript shells, and prompt injection. Robots errors and redirects in the benchmark are frozen fetch outcomes; the HTTP policy itself is tested separately with mocked responses.

The benchmark emits raw per-case expected and actual fields, evidence validity, latency and failures, plus category summaries and denominators. Accepted fields with invalid evidence count as incorrect. It reports precision, recall, abstention, conflict detection, evidence validity, latency and failure rates. Setup/review time and cost per accepted field remain null until measured. These synthetic fixtures are functional checks, not evidence of live-site accuracy, competitor performance or calibrated confidence.

## Scope and current gaps

JavaScript-only sites, nested values, synonyms, unlabeled values, pages requiring cookies, and PDFs are unsupported. A JS shell yields missing fields. Hidden detection handles the HTML `hidden` attribute, hidden ancestors, `aria-hidden=true`, and inline `display:none`, `visibility:hidden`, and `opacity:0`. External stylesheets, CSS classes, media queries, pseudo-elements, inherited computed styles beyond these inline rules, and script-driven visibility are unsupported; a page using them can still yield a false visible candidate. Browser subresource SSRF is avoided by having no browser or asset loader. The local API has static bearer keys, synchronous jobs, no request-level rate limit, no TLS termination, no job queue, and no managed recovery for abandoned claims. SQLite is not encrypted at rest. Do not expose the service publicly without adding those controls, operational monitoring and a deployment network egress policy.

## Reference review and reuse

Read-only references inspected: `D:\Ryze-MCP-Enterprise\Ryze-MCP\ryze_tools\Research\brand_scraper.py`, its regular and adversarial tests, `cache\ai_cache.py`, `config.py`, `audits\mcp_repo_audit.md`, `audits\crawl4ai_capabilities.md`, `audits\firecrawl_free_tier.md`; and `D:\Ryze-MCP_Latest\Ryze-MCP_Latest-main\Ryze-MCP\ryze_tools\Research\_brand_cache.py` plus its MIT license. The Enterprise frontend's `AGENTS.md` does not apply to this separate project. No code was copied. Ideas reused conceptually: bounded crawl, robots handling, static HTML first, and adversarial fixture coverage. Fetching, evidence verification, API, storage and benchmark were rewritten. The Ryze domain/depth cache scheme is intentionally absent because it cannot isolate tenants or schemas.

## Fair competitor test proposal

Freeze a consented set of public sites and independent labeled evidence, with separate development and held-out groups. Give every adapter the same seed, schema, page and time limits; record all provider calls, costs, returned sources and manual review minutes. Score only values that the independent reviewer can support from frozen captures; report abstentions, conflicts, error rates and confidence intervals alongside precision, recall, latency and cost per accepted field. `BenchmarkAdapter` provides the interface. Ryze, Firecrawl, Zyte, Diffbot and crawler-plus-model adapters are deliberately unconfigured; no competitor result is claimed. Before a live comparison, obtain the user's approval for the exact target list, request volume, accounts, estimated charges and impact, then configure provider credentials and honor each site's access rules.

For a paid pilot: add a controlled egress proxy/firewall, external authentication and TLS, a job queue, encrypted tenant-isolated storage, retention enforcement with backups, rate and cost limits, auditability, operational metrics, broader labeled evaluation, and a reviewed acceptable-use process. Reassess the commercial hypothesis using the held-out benchmark before expanding the platform.

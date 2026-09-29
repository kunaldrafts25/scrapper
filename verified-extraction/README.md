# Verified Extraction

Verified Extraction 0.4.0 is a standalone, local scraper for explicit scalar facts on public HTML pages. It keeps original response bytes and returns a field as `verified` only when its label, value, DOM location, source URL, fetch time, and byte hash can be checked against the capture. It can abstain or report conflicting values. Verification means **the captured page supports the claim**; it does not establish that the website is correct or current.

## Install and run locally

Python 3.11 or newer is required. In PowerShell:

```powershell
cd D:\scrapper\verified-extraction
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
$env:VE_KEYS='{"local":"replace-with-a-long-random-local-secret"}'
$env:VE_DB='data/verified_extraction.sqlite3'
.\.venv\Scripts\python.exe -m uvicorn verified_extraction.api:app --host 127.0.0.1 --port 8000
```

`VE_KEYS` is a JSON map of tenant IDs to bearer keys. Keep the key and database private. The service has no TLS, external authentication, tenant quotas, or durable queue; bind it to loopback only. `VE_DB` is optional and defaults to the path above. The database stores captures, request settings, results, reviews, sessions, and host leases. Old jobs and captures are removed after seven days on subsequent API access; SQLite free pages and backups are not securely erased.

## Submit and review a job

The following example **makes live requests** to `example.com` when submitted. Use only a site you are authorized to fetch. In a second PowerShell window:

```powershell
$headers = @{Authorization='Bearer replace-with-a-long-random-local-secret'}
$body = @{
  url='https://example.com/'
  schema=@{type='object';properties=@{
    support=@{type='string';title='Support'}
    plan_price=@{type='number';title='Plan price';'x-unit'='month'}
    usage_limit=@{type='integer';title='Usage limit'}
  }}
  options=@{max_pages=1;max_depth=0;deadline_seconds=15;max_http_requests=10}
  idempotency_key='example-1'
} | ConvertTo-Json -Depth 8
$result = Invoke-RestMethod -Uri http://127.0.0.1:8000/v1/jobs -Method Post -ContentType application/json -Headers $headers -Body $body
$result.job_id
```

This page is unlikely to contain those labels, so expect `missing` fields. For a page containing `<p>Support: Email</p>`, the `support` field has `state: "verified"`, `value: "Email"`, and evidence with its exact excerpt, locator, URL, fetch time, and SHA-256 hash. Each job returns `fields`, `pages`, `errors`, and `usage`; `usage.http_requests_started` includes robots and redirects. A zero `estimated_internal_cost_usd` is a placeholder, not a measured total cost.

Open `http://127.0.0.1:8000/review`. Enter the job ID, bearer key, and reviewer ID. For each field, select **Start review**, **Stop review**, and **Save verdict**. Sessions cannot overlap for one reviewer or job, and later edits remain in history. The page keeps the key in memory, sends it in the Authorization header, and does not put it in the URL. The authenticated API also exposes:

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/jobs/{job_id}` | Result and page log |
| `GET /v1/jobs/{job_id}/review-evidence` | Source nodes and captured text |
| `POST /v1/jobs/{job_id}/reviews/{field}/start` and `/stop` | Timed review session |
| `PUT /v1/jobs/{job_id}/reviews/{field}` | Save verdict or correction after a session |
| `GET /v1/jobs/{job_id}/reviews`, `/review-history`, `/labels` | Current reviews, edits, machine-assisted export |
| `GET /v1/jobs/{job_id}/snapshots/{hash}` and `/raw` | Decoded and original-byte capture |
| `DELETE /v1/jobs/{job_id}` | Remove one job and its stored review/capture data |
| `GET /v1/metrics` | Tenant-scoped aggregate counters |

The `/labels` export contains machine output and reviewer verdicts. Keep it away from blind labelers. It does **not** by itself establish a corrected final vendor row; the evaluation uses a separate, source-validated `corrected_row.json`.

## Extraction and access boundaries

- Input is a limited JSON Schema object with **3–10** scalar `string`, `number`, `integer`, or `boolean` properties. A property title is its exact label; otherwise underscores in the property name become spaces. Supported optional numeric hints are `x-unit` (`month`, `year`, `day`, `user`, `seat`, `GB`) and `x-currency` (`USD`, `EUR`, `GBP`, `INR`). Unsupported schema keywords return HTTP 422.
- Extraction recognizes explicit `Label: value` or `Label - value` text in visible paragraphs, list items, headings, table rows, and individual definition elements, plus matching **top-level** JSON-LD scalar keys. It does not join a separate `<dt>` label to a `<dd>` value, perform general semantic extraction, or join facts across plans. A `$` sign is parsed as USD by the product; the independent customer-task rubric requires explicit currency context.
- Large exact integers and non-integral decimals are returned as canonical strings with `numeric_encoding`; no float conversion or currency conversion is performed. Conflicting supported values remain `conflicting`. A `verified` claim is source-backed, not externally fact-checked.
- Per job: 1–8 pages, depth 0–2, 2–45 seconds, 1–50 HTTP GETs including robots checks and redirects; defaults are 3 pages, depth 1, 20 seconds, and 20 GETs. Each response is capped at 1 MB and each request at four redirects. Page hints start at depth one.
- The fetcher checks exact host scope, public DNS addresses, redirects, and robots rules before page requests. Robots 200 is parsed; 404 means no rules; other statuses fail closed. A denial stops the crawl. Requests to one host use a cross-process renewable SQLite lease and at least 0.5 seconds between actual starts, or a longer robots delay. The child process is killed on whole-job deadline. No browser, asset fetcher, proxy, account login, CAPTCHA bypass, or paid model is included.
- JavaScript-rendered pages, PDFs, nested JSON-LD values, synonyms, unlabeled prices, and logged-in content are unsupported. Visibility checks cover HTML `hidden`, hidden ancestors, `aria-hidden`, and inline display/visibility/opacity styles, but not external CSS or script-driven visibility. The local API is not ready for public deployment.

## Offline verification

These commands use local fixtures and mocked access behavior; they make no live-site or paid-provider calls:

```powershell
python -m pytest -q -p no:cacheprovider
python -m benchmark.run --suite v2 --split all
python -m benchmark.run --suite smoke --split all
python -m benchmark.measure_reliability
python -m benchmark.measure_host_policy
python -m benchmark.validate_targets --csv benchmark/candidate_targets.csv --mode proposal
python -m benchmark.validate_targets --csv benchmark/candidate_targets.csv --mode execution
```

The last command is **expected to reject** the pending inventory. The 16-case v2 benchmark and six older smoke cases are synthetic regression fixtures, not measurements of real-site accuracy or customer value. The host-policy measurement records actual offline concurrent-worker request start and finish times. Review effort, live latency, and total cost remain unmeasured until an approved evaluation.

## Real-site evaluation status

The [evaluation plan](EVALUATION_PLAN.md), [exact 24-site approval package](benchmark/APPROVAL_PACKAGE.md), and [separate IANA smoke plan](ONE_SITE_TEST_PLAN.md) are proposals. The candidate inventory fixes 12 development and 12 held-out hostnames, but its access basis, robots status, and approval record remain pending. **No real-site evaluation or pilot decision is complete.** No competitor adapter has been configured or measured.

After exact-scope approval and the proposed 15 October 2026 window, the approved process can count preflight, robots, and redirects against each site's GET ceiling, freeze original bytes and code revision, collect independent blind labels and adjudication, and record paired manual and assisted review. `benchmark.export_local` creates blind, manual-baseline, and corrected-row templates from an approved job. Its manifest leaves the separate preflight GET count and access-policy audit pending for the evaluator to record. `benchmark.replay` reports partial results as `incomplete`; `--finalize` requires all 24 approved outcomes, corrected rows, combined request and cost measurements, the access audit, and a customer-confirmed labor rate and thresholds before issuing a go/no-go decision. See the evaluation plan for commands and scoring rules.

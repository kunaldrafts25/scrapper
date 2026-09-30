# Verified Extraction

Verified Extraction 0.4.1 is a standalone, local scraper for explicit scalar facts on public HTML pages. It keeps original response bytes and returns a field as `verified` only when its label, value, DOM location, source URL, fetch time, and byte hash can be checked against the capture. It can abstain or report conflicting values. Verification means **the captured page supports the claim**; it does not establish that the website is correct or current.

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

`VE_KEYS` is a JSON map of tenant IDs to bearer keys. Keep the key and database private. The service has no TLS, external authentication, tenant quotas, or durable queue; bind it to loopback only. `VE_DB` is optional and defaults to the path above. The database stores captures, request settings, results, reviews, sessions, and host leases. Old jobs and captures are removed after seven days on subsequent API access; SQLite free pages and backups are not securely erased. Evaluation bundles under ignored `benchmark/local/` require manual deletion within seven days; the current run's deadline is **7 October 2026**.

## Use the local workbench

Open `http://127.0.0.1:8000/review` after starting the server. Enter a public HTML URL, your configured bearer key, and a reviewer ID. Define three to ten fields with unique keys, exact page labels, and scalar types. For a plan comparison, enter the **named plan** as well; it bounds plan-card and table-column extraction to that plan. Expand **Bounded crawl options** to adjust pages, depth, deadline, and GET limit, then select **Run extraction**. The workbench shows the job ID, status, request count, elapsed time, page and access errors, each field's state and evidence, and the original captured text as inert text. Copy the job ID to reopen the result later with the same tenant key.

For each field, select **Start review**, inspect the capture, select **Stop review**, choose a verdict and any correction, then **Save decision**. The review history lists sessions and edits. **Download review export** produces machine-assisted review data; keep it away from independent blind labelers. The workbench reports network, authorization, validation, and partial-result states. It uses the responsive panel and source-inspection pattern of [ThreeUI Community](https://github.com/MengTo/threeui/blob/main/src/App.tsx), whose [MIT license](https://github.com/MengTo/threeui/blob/main/LICENSE) covers its application code. The adaptation is plain HTML, CSS, and JavaScript, with no ThreeUI runtime, CDN, remote assets, or decorative effect required.

## Submit through the API

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

Sessions cannot overlap for one reviewer or job, and later edits remain in history. The workbench keeps the key in page memory, sends it in the Authorization header, and does not put it in the URL. A per-response nonce Content Security Policy restricts scripts and styles; captured content enters the DOM as text. The authenticated API also exposes:

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
- Extraction recognizes explicit `Label: value` or `Label - value` text in visible paragraphs, list items, headings, table rows, and individual definition elements, plus matching **top-level** JSON-LD scalar keys. With `target_plan`, it also binds an exact plan heading to a local card or an unspanned table column, keeping price and billed period on that plan. It does not join a separate `<dt>` label to a `<dd>` value, perform general semantic extraction, or join facts across plans. Plan-card prices require an explicit currency and one billing choice; a dollar sign alone does not meet that rule. The older generic label parser still interprets `$` as USD, so generic output needs human review before a procurement row is accepted.
- Large exact integers and non-integral decimals are returned as canonical strings with `numeric_encoding`; no float conversion or currency conversion is performed. Conflicting supported values remain `conflicting`. A `verified` claim is source-backed, not externally fact-checked.
- Per job: 1–8 pages, depth 0–2, 2–45 seconds, 1–50 HTTP GETs including robots checks and redirects; defaults are 3 pages, depth 1, 20 seconds, and 20 GETs. Each response is capped at 1 MB and each request at four redirects. Page hints start at depth one.
- The fetcher checks exact host scope, public DNS addresses, redirects, and robots rules before page requests. Robots 200 is parsed; 404 means no rules; other statuses fail closed. A denial stops the crawl. Requests to one host use a cross-process renewable SQLite lease and at least 0.5 seconds between actual starts, or a longer robots delay. The child process is killed on whole-job deadline. A durable SQLite start counter now accompanies timeout and worker-failure errors as `http_requests_started`, including robots and redirects. No browser renderer, asset fetcher, proxy, account login, CAPTCHA bypass, or paid model is included.
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

The last command is **expected to reject** the inventory CSV because its approval and robots fields are still pending, even though a separate user instruction authorized the smoke and development run. The 16-case v2 benchmark and six older smoke cases are synthetic regression fixtures, not measurements of real-site accuracy or customer value. The host-policy measurement records actual offline concurrent-worker request start and finish times.

## Real-site evaluation status

The [evaluation plan](EVALUATION_PLAN.md), [exact 24-site approval package](benchmark/APPROVAL_PACKAGE.md), and [IANA smoke plan](ONE_SITE_TEST_PLAN.md) define the scope. The user authorized the smoke and 12 development hosts for **30 September 2026**; [the live report](benchmark/LIVE_DEVELOPMENT_2026-09-30.md) records outcomes and limits. The smoke used two GETs and abstained on all three fields. Four development sites were captured, five stopped on access or size controls, and three hit deadlines. All 24 fields in the four completed jobs abstained. The 12 held-out product pages remain uncaptured because the analyst hourly rate and threshold confirmation are pending. **No complete real-site accuracy or pilot decision exists.** No competitor adapter has been configured or measured.

The four development bundles contain original bytes, request settings, code revision, and blank blind-label, manual-baseline, and corrected-row templates. Offline repair replay now accepts seven source-bound plan or billing-period claims across those four frozen sites; the [live report](benchmark/LIVE_DEVELOPMENT_2026-09-30.md) separates this from the original live result. The original three deadline attempts predate the durable counter, so their exact GET counts cannot be reconstructed. Source-only notes are provisional, and independent labels and paired review are still outstanding. `benchmark.replay` reports partial results as `incomplete`; `--finalize` requires all 24 approved outcomes, corrected rows, combined request and cost measurements, the access audit, and a customer-confirmed labor rate and thresholds before issuing a go/no-go decision. See the evaluation plan for commands and scoring rules.

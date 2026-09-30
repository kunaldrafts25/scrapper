# Verified Extraction

Verified Extraction 0.6.0 is a standalone local app for inspecting public HTML pages. Paste one link to get an extractive overview and grouped facts with source evidence. It recognizes article, organization/service, product, and SaaS pricing signals, and falls back to a general page when type is uncertain. Advanced jobs still extract exact scalar fields. Every accepted claim is rechecked against retained response bytes. “Found on page” means the capture supports the text; it does not prove the website is correct or current.

## Install and run locally

Python 3.11 or newer is required. In PowerShell:

```powershell
cd D:\scrapper\verified-extraction
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
$env:VE_LOCAL_UI='1'
$env:VE_DB='data/verified_extraction.sqlite3'
.\.venv\Scripts\python.exe -m uvicorn verified_extraction.api:app --host 127.0.0.1 --port 8000
```

`VE_LOCAL_UI=1` enables keyless access only for same-origin requests from a loopback client and loopback Host. Keep the server bound to `127.0.0.1` on a trusted machine. Public deployment is unsupported; it would need TLS, real authentication, quotas, a durable job queue, and an isolated execution environment. For authenticated API clients, configure `VE_KEYS` as a JSON map such as `{"team":"a-long-random-secret"}`. `VE_DB` is optional and defaults to `data/verified_extraction.sqlite3`. The database stores captures, results, request settings, reviews, and host leases. Expired jobs, captures, and reviews are purged on server startup, hourly while running, and on relevant API access. SQLite deletion does **not** securely erase free pages, WAL files, backups, or disk snapshots. Ignored evaluation bundles under `benchmark/local/` require separate manual deletion within seven days. The September live captures were deleted with approval.

## Use the local workbench

Open `http://127.0.0.1:8000/`, paste a permitted public HTML URL, and select **Get page info**. No schema, reviewer ID, or key is needed in local mode. The quick job fetches one page with a 20-second deadline and a 10-GET ceiling including robots and redirects. It groups page, structured entity, detail, offer, and named-plan facts where supported. The overview is an excerpt from the page or its structured data, never generated prose. Each claim has an **Inspect source** panel; conflicts appear as ambiguous and missing facts say they could not be confirmed. The page-type label is uncertain when signals disagree. Recent pages reopen with one click, and **Delete this job** removes a stored job. **Cancel** stops an in-flight quick job through its claim lease; a retry uses the same key until success or cancellation, preventing accidental duplicate jobs.

Open **More options** to define three to ten exact scalar fields, name a pricing plan, set bounded crawl options, enter an API key when local UI mode is disabled, or reopen a saved job by ID. Request counts, elapsed time, exports, and timed review controls stay under advanced details. Local UI jobs are stored under their own tenant and cannot be opened with another tenant's bearer key.

Open **View source evidence** for a fact to inspect the capture. **Review this fact** reveals the timed review controls. The review history lists sessions and edits; **Download review export** produces machine-assisted review data. Keep that export away from independent blind labelers. The workbench reports network, authorization, validation, and partial-result states. It uses the responsive panel and source-inspection pattern of [ThreeUI Community](https://github.com/MengTo/threeui/blob/main/src/App.tsx), whose [MIT license](https://github.com/MengTo/threeui/blob/main/LICENSE) covers its application code. The adaptation is plain HTML, CSS, and JavaScript, with no ThreeUI runtime, CDN, remote assets, or decorative effect required.

## Submit through the API

In local UI mode, a same-origin browser can submit `POST /v1/quick-jobs` with `{"url":"https://permitted.example/page","idempotency_key":"your-unique-key"}`. For a PowerShell API client, configure `VE_KEYS` and send a bearer key as shown below. The example URL is a placeholder; do not submit it unchanged.

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

Sessions cannot overlap for one reviewer or job, and later edits remain in history. If an API key is entered under **More options**, the workbench keeps it in page memory, sends it in the Authorization header, and does not put it in the URL. A per-response nonce Content Security Policy restricts scripts and styles; captured content enters the DOM as text. The API also exposes:

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/quick-jobs` | Create a one-link job (`{"url":"https://...","idempotency_key":"unique-key"}`); `automatic_result.version` is `1.0` |
| `POST /v1/quick-jobs/cancel` | Cancel a pending quick job by `idempotency_key` |
| `GET /v1/recent-jobs` | List the 12 most recent jobs in this tenant |
| `GET /v1/jobs/{job_id}` | Result and page log |
| `GET /v1/jobs/{job_id}/review-evidence` | Source nodes and captured text |
| `POST /v1/jobs/{job_id}/reviews/{field}/start` and `/stop` | Timed review session |
| `PUT /v1/jobs/{job_id}/reviews/{field}` | Save verdict or correction after a session |
| `GET /v1/jobs/{job_id}/reviews`, `/review-history`, `/labels` | Current reviews, edits, machine-assisted export |
| `GET /v1/jobs/{job_id}/snapshots/{hash}` and `/raw` | Decoded and original-byte capture |
| `DELETE /v1/jobs/{job_id}` | Remove one job and its stored review/capture data |
| `GET /v1/metrics` | Tenant-scoped aggregate counters |

The one-link response retains the existing job envelope and adds `automatic_result` version `1.0`:

```json
{
  "version": "1.0",
  "page_type": {"value": "saas_pricing", "confidence": "source_signals", "alternatives": []},
  "overview": {"state": "missing", "text": null, "evidence": []},
  "groups": ["Page", "Plan: Team"],
  "facts": [{"group": "Plan: Team", "key": "listed_price", "label": "Price",
    "state": "found_in_source", "value": 29, "currency": "USD", "unit": "month",
    "billing_period": "year", "variant": "Team", "evidence": [{"source_url": "https://example.org/pricing",
      "fetched_at": "2026-09-30T00:00:00Z", "snapshot_hash": "...", "locator": "...",
      "excerpt": "Price: USD 29 per user/month, billed annually", "raw_value": "USD 29"}]}],
  "errors": [], "fetched_at": "2026-09-30T00:00:00Z"
}
```

This is an illustrative shape, not a recorded site result. Fact states are `found_in_source`, `ambiguous`, `missing`, or `blocked`; ambiguous facts carry alternatives and no accepted value. Page and access errors are also present in the job envelope. The evidence locator, full SHA-256 hash, fetch time, and raw text come from the actual capture.

The `/labels` export contains machine output and reviewer verdicts. Keep it away from blind labelers. It does **not** by itself establish a corrected final vendor row; the evaluation uses a separate, source-validated `corrected_row.json`.

## Extraction and access boundaries

- Input is a limited JSON Schema object with **3–10** scalar `string`, `number`, `integer`, or `boolean` properties. A property title is its exact label; otherwise underscores in the property name become spaces. Supported optional numeric hints are `x-unit` (`month`, `year`, `day`, `user`, `seat`, `GB`) and `x-currency` (`USD`, `EUR`, `GBP`, `INR`). Unsupported schema keywords return HTTP 422.
- Extraction recognizes explicit `Label: value` or `Label - value` text in visible paragraphs, list items, headings, table rows, and individual definition elements, plus matching **top-level** JSON-LD scalar keys. With `target_plan`, it also binds an exact plan heading to a local card or an unspanned table column, keeping price and billed period on that plan. It does not join a separate `<dt>` label to a `<dd>` value, perform general semantic extraction, or join facts across plans. Plan-card prices require an explicit currency and one billing choice; a dollar sign alone does not meet that rule. The older generic label parser still interprets `$` as USD, so generic output needs human review before a procurement row is accepted.
- The **automatic one-link result** additionally reads page metadata, visible headings and paragraphs, conservative labeled details and definition/table pairs, nested JSON-LD `@graph` entities tied to the visible title and page URL when one is supplied, product offers, and separate named-plan cards. It ranks page facts and plans ahead of lower-priority details, caps output at 60 facts, and exposes more facts on demand. It does not execute scripts, infer currency from `$`, join unrelated entities, or turn model text into claims. A product offer must state a supported currency. Page-type detection uses structured `@type`, Open Graph type, article structure, and plan cards; disagreements yield an uncertain general page. The current automatic output is a source-backed first pass, not complete semantic understanding.
- Large exact integers and non-integral decimals are returned as canonical strings with `numeric_encoding`; no float conversion or currency conversion is performed. Conflicting supported values remain `conflicting`. A `verified` claim is source-backed, not externally fact-checked.
- Per job: 1–8 pages, depth 0–2, 2–45 seconds, 1–50 HTTP GETs including robots checks and redirects; defaults are 3 pages, depth 1, 20 seconds, and 20 GETs. Each response is capped at 1 MB and each request at four redirects. Page hints start at depth one.
- The fetcher checks exact host scope, public DNS addresses, redirects, and robots rules before page requests. Robots 200 is parsed; 404 means no rules; other statuses fail closed. A denial stops the crawl. Requests to one host use a cross-process renewable SQLite lease and at least 0.5 seconds between actual starts, or a longer robots delay. The child process is killed on whole-job deadline. A durable SQLite start counter now accompanies timeout and worker-failure errors as `http_requests_started`, including robots and redirects. No browser renderer, asset fetcher, proxy, account login, CAPTCHA bypass, or paid model is included.
- JavaScript-rendered pages, PDFs, arbitrary nested JSON-LD properties, synonyms, unlabeled prices, and logged-in content are unsupported. Visibility checks cover HTML `hidden`, hidden ancestors, `aria-hidden`, and inline display/visibility/opacity styles, but not external CSS or script-driven visibility. The local API is not ready for public deployment.
- The quick flow supports nested JSON-LD `@graph` and page-linked product offers; the advanced custom-schema parser still supports only top-level JSON-LD scalar keys. External CSS, JavaScript-rendered content, PDFs, login-only pages, and currency inference from a dollar sign remain unsupported. The browser is used only to test the app UI, never to render scraped targets. Quick jobs are synchronous but capped at 20 seconds; the UI shows progress text and can cancel the worker through the claim lease. Repeated explicit retries after a failed job can cause another bounded fetch.

## Offline verification

These commands use local fixtures and mocked access behavior; they make no live-site or paid-provider calls:

```powershell
python -m pytest -q -p no:cacheprovider
python -m benchmark.run --suite v2 --split all
python -m benchmark.run --suite smoke --split all
python -m benchmark.run_one_link
python -m benchmark.measure_reliability
python -m benchmark.measure_host_policy
python -m benchmark.validate_targets --csv benchmark/candidate_targets.csv --mode proposal
python -m benchmark.validate_targets --csv benchmark/candidate_targets.csv --mode execution
python -m benchmark.validate_one_link_targets --mode proposal
python -m benchmark.validate_one_link_targets --mode execution
python -m pip wheel . --no-deps --no-build-isolation -w benchmark/local/dist
python benchmark/browser_e2e.py
```

Both execution-mode inventory checks are **expected to reject** their pending proposals. The 16-case v2 benchmark, six older smoke cases, and nine one-link cases are synthetic regressions, not real-site accuracy or usefulness measurements. `browser_e2e.py` uses local Chrome with a mocked fetcher and a temporary profile/database; it sends no target-site requests. The host-policy measurement records offline concurrent-worker request start and finish times.

## Real-site evaluation status

The [evaluation plan](EVALUATION_PLAN.md), [exact 24-site approval package](benchmark/APPROVAL_PACKAGE.md), and [IANA smoke plan](ONE_SITE_TEST_PLAN.md) define the scope. The user authorized the smoke and 12 development hosts for **30 September 2026**; [the live report](benchmark/LIVE_DEVELOPMENT_2026-09-30.md) records outcomes and limits. The smoke used two GETs and abstained on all three fields. Four development sites were captured, five stopped on access or size controls, and three hit deadlines. All 24 fields in the four completed jobs abstained. The 12 held-out product pages remain uncaptured because the analyst hourly rate and threshold confirmation are pending. **No complete real-site accuracy or pilot decision exists.** No competitor adapter has been configured or measured.

Before retention cleanup, an offline repair replay accepted seven source-bound plan or billing-period claims across the four frozen development sites; the [live report](benchmark/LIVE_DEVELOPMENT_2026-09-30.md) separates this from the original live result. Those bundles have since been deleted with the user's approval. The original three deadline attempts predate the durable counter, so their exact GET counts cannot be reconstructed. Source-only notes were provisional; independent labels and paired review were not completed before deletion. `benchmark.replay` reports partial results as `incomplete`; `--finalize` requires 24 approved outcomes, corrected rows, combined request and cost measurements, the access audit, and a customer-confirmed labor rate and thresholds before issuing a go/no-go decision. A new approved capture would be needed for further real-site scoring. See the evaluation plan for commands and scoring rules.

The new one-link task has a separate [24-page approval proposal](benchmark/ONE_LINK_APPROVAL.md) and [exact CSV inventory](benchmark/one_link_targets.csv), with 12 development and 12 untouched held-out pages across four types. It is **unapproved**. No new real-site pages were fetched for this release, and independent labels are unavailable. Consequently accepted-claim precision, useful-fact recall, wrong-entity and wrong-plan rates, usable-page rate, live p50/p95 latency, measured cost, and a pilot decision are **unmeasured**. Synthetic passes cannot satisfy the preregistered held-out thresholds.

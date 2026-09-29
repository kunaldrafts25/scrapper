# Controlled real-site evaluation proposal (not yet authorized)

## Proposed first customer and task

Target customer: a small B2B software procurement team comparing public pricing terms for a vendor shortlist. One row means **one named paid plan on one vendor site**, with six separately supported fields: named plan, listed recurring price, explicit currency, billing period, usage or seat limit, and plan-specific support channel. `benchmark/procurement_schema.json` fixes the field names and types before any site output is inspected. A customer may pay if the reviewed row and inspectable capture save analyst time and reduce unsupported copy/paste claims. This is a hypothesis, not measured demand.

The plan name is fixed in the target inventory before capture. If the site has several plans, use only that named plan. Never borrow a price, period, limit, or support promise from another plan to complete the row. If the named plan disappears, mark its fields missing and log the site; do not silently substitute another plan. Use the ordinary public recurring list price for the specified billing period; promotional discounts, quote-only prices, and annual prepayment are separate claims. If the ordinary price cannot be isolated, mark price conflicting or missing. Currency must be explicit in the source context; do not infer USD merely from `$`. Shared support terms count only when the site explicitly says they apply to the named plan or all plans. Conflicting pages remain conflicting until a blind adjudicator documents the reason for a final label. The present extractor has no automatic plan-entity join, so cross-plan mixing is a measured error, not a verified row.

The current extractor requires explicit field labels. The evaluation should include ordinary pricing pages, including ones that do **not** use its expected labels; abstention and reviewer effort must be counted honestly. Do not preselect sites because they are easy for the extractor.

## Sample and access proposal

1. Prefer the procurement team's own shortlist. In its absence, `benchmark/candidate_targets.csv` freezes **24 proposed distinct vendor hostnames** with 12 development and 12 held-out assignments before any scraper output. Its seed paths and named plans are unverified offline hypotheses, not approved targets. The customer must accept the task and list. `benchmark/validate_targets.py` deliberately rejects the candidate file while access, robots, date, and approval fields are unresolved.
2. Include public B2B software pricing pages across workflow, project management, design, developer, storage, support, CRM, collaboration, and observability categories. Exclude logged-in content, personal data, paywalls, CAPTCHAs, non-HTML sources, and sites whose terms or robots policy prohibit access. Log every rejection in `benchmark/rejection_log.csv` with reason and timestamp before replacement. Replace only from a predeclared same-category reserve approved before extraction; preserve the original split and never replace a hard site because the extractor performs poorly.
3. Keep all pages from one hostname in one split. Freeze code revision, fixed schema, named plan, exact seed, allowed hostnames, page hints, capture date, and selected options. Do not tune on held-out output.
4. Proposed local crawl maximum: **3 HTML pages/site**, depth **1**, **20-second** deadline, four redirects/request, 1 MB/response, one leased in-flight request per host across workers, and at least 0.5 seconds between actual request starts or any longer robots crawl delay. The enforceable **20 HTTP GET/site** ceiling includes up to five robots/redirect requests and up to fifteen page/redirect requests on a single allowed host. Across 24 sites, the absolute proposed maximum is 480 GETs; ordinary no-redirect operation is at most 96. The approved inventory must increase its bound if more hosts are allowed. Stop on denial and do not retry around it.
5. Capture all sites in a one-day window, then freeze bytes and metadata. Rerun extraction only from frozen captures. Keep public-site captures local and tenant isolated. Retain them for the documented seven-day default unless the approved evaluation records a different retention period.

If a plan name or term is not explicitly supported in the captured source, label it missing, conflicting, or unverified. Numeric labels retain currency and period. The fixed schema and per-site plan name belong in the approved inventory before capture.

## Independent labeling and review

- Two distinct people independently label each held-out site from frozen captures **without viewing machine values**. Save `labels.reviewer-a.json` and `labels.reviewer-b.json`; a third person records `adjudication.json` with each agreement or disagreement, reason, final decision, identity and seconds. The loader preserves both originals and rejects a missing adjudication record. Development sites use one blind `labels.json`. Every verified literal label needs the exact raw value, unit/currency, source URL, SHA-256 and visible excerpt from the same claim. An interpretation that cannot be tied to a literal captured value stays unverified with a note until a documented adjudication resolves it; it cannot silently become a verified label.
- A separate analyst completes `manual_baseline.json` on the same frozen pages without seeing machine output, timing the whole six-field row and recording all values and evidence. The scorer compares it with adjudicated truth to calculate minutes per site and field error rate. A separate reviewer uses `/review` to judge the machine output (`correct`, `wrong`, `unsupported`, `conflicting`, `uncertain`) and enters corrections. Explicit start/stop sessions measure nonoverlapping assisted-review seconds per field; every edit remains in review history. These machine-assisted verdicts are not substituted for blind ground truth.
- Count a supported-looking value as wrong if its source URL, fetch timestamp, hash, locator, label or parsed value cannot be validated against the corresponding frozen capture.
- Record per-site failures: discovery, tables, metadata, visibility, encoding, JavaScript shell, unsupported schema, access policy, numeric context, and reviewer effort. Keep example failures, with customer-sensitive details redacted before external sharing.

## Offline bundle and replay commands

After an **approved** local job exists, export its captures and a blind-label template without another network request:

```powershell
python -m benchmark.export_local --db data/verified_extraction.sqlite3 --tenant TENANT --job-id JOB_ID --site-id SITE_ID --split development --category pricing --plan-name "NAMED PLAN" --permission-note "approval record ID" --output benchmark/local/SITE_ID
```

The bundle contains `manifest.json` with code revision and live elapsed time, byte captures (`.bin`), inert decoded text (`.txt`), `labels.template.json`, and `manual_baseline.template.json`. Held-out bundles also contain `adjudication.template.json`. Reviewers fill the appropriate files; the loader rejects unsupported values, hash mismatches, missing fields and incomplete held-out adjudication. After blind labels are sealed, save the API `/labels` export as `assisted_reviews.json` in the bundle. Keep that machine-containing file away from blind labelers. Blind labeling seconds, manual baseline seconds, and assisted-review sessions are reported separately. Run local replay with:

```powershell
python -m benchmark.replay --bundles benchmark/local --split development --adapter local --output benchmark/local/development-results.json
python -m benchmark.replay --bundles benchmark/local --split held_out --adapter local --output benchmark/local/held-out-results.json
```

The output includes raw per-site result envelopes, expected labels, evidence checks, metric denominators, category summaries, examples of errors, and a deterministic site bootstrap interval. Captures and labels stay local and are ignored by Git. The interval is descriptive for this small sample, not a calibrated guarantee. Offline replay latency excludes live network latency; the original job envelope records observed end-to-end time separately.

## Metrics and decision rule

Report field-level precision and recall, verified evidence validity, abstention, conflict detection, access/fetch failures, live and offline elapsed time, manual baseline minutes and error rate, assisted review minutes per correct accepted field, and cost per accepted field **when measured**. Report numerators, denominators, by-site and by-category results. A verified field with invalid evidence or a fact from another plan is incorrect. Do not infer a cost from the current placeholder `0.0` internal cost; instrument compute and provider spend first.

Pre-registered exploratory gate for the 12 held-out sites: (a) zero wrong accepted plan, price, currency or period claims and 100% evidence-valid accepted fields; (b) at least 95% overall field precision with at least 24 accepted fields, and at least 50% accepted-field recall among independently verified expected values; (c) assisted review time at least 25% below the paired manual baseline and no higher field error rate; (d) no robots/scope violation and at most two access/fetch failures; (e) total measured cost at most USD 5 per completed reviewed vendor row, including analyst labor at a pre-agreed hourly rate and any provider charges. The customer must ratify the labor rate and these thresholds before held-out review. A no-go or redesign follows if any critical criterion fails. Synthetic results cannot satisfy this gate.

## Equivalent alternative tests

For Firecrawl, Zyte, Diffbot and a crawler-plus-model baseline, use the same frozen task list, schema, site split and scoring rubric. Where a provider accepts frozen HTML, run a capture-locked extraction comparison. For a separate end-to-end comparison, obtain approval for each provider's account, exact sites, requests, maximum spend and data transfer; log actual calls, returned source references, charges, limits, latency and manual review minutes. Record unavoidable differences in crawl or rendering limits. No adapter is configured or benchmarked yet; unconfigured adapters must not emit performance scores.

## Approval gate and paid pilot

Before any live crawl or provider call, replace candidate statuses with a customer-approved list and fill the access basis, robots check, exact per-site plan, request bounds, date window, account, data destination, maximum charges and impact. Run `python -m benchmark.validate_targets --csv benchmark/candidate_targets.csv`; it must pass before the scope is presented for explicit approval. The one-site IANA smoke test also remains unapproved and separate from this customer task. Before public deployment, separately design authentication, TLS, a durable queue, tenant quotas, request and spend limits, encrypted storage/backups, retention enforcement, monitoring and controlled egress. The current local MVP is not ready for public deployment.

# Controlled real-site evaluation proposal (not yet authorized)

## Proposed first customer and task

Target customer: a small B2B software procurement team comparing public pricing terms for a vendor shortlist. Proposed job: produce a reviewed row per vendor with (1) one named paid plan's listed price, (2) billing period, (3) explicitly stated usage or seat limit, and (4) support channel. A customer may pay if the reviewed row and inspectable capture save analyst time and reduce unsupported copy/paste claims. This is a hypothesis, not measured demand.

The current extractor requires explicit field labels. The evaluation should include ordinary pricing pages, including ones that do **not** use its expected labels; abstention and reviewer effort must be counted honestly. Do not preselect sites because they are easy for the extractor.

## Sample and access proposal

1. Obtain a customer-approved shortlist of **24 distinct public vendor hostnames**, with one public seed URL per vendor and a written permission/access basis. The actual URL list is still missing; `benchmark/approved_targets.template.csv` is the reviewable approval artifact. No live requests are authorized by this plan.
2. Exclude logged-in content, personal data, paywalls, CAPTCHAs and sites whose terms or robots policy prohibit the proposed access. Preserve a rejection log so easy sites are not silently substituted.
3. Assign 12 hostnames to development and 12 to held-out **before** inspecting outputs. Keep all pages from one hostname in one split. Freeze the code revision, input schema, exact seed, allowed hostnames, page hints, capture date and selected options.
4. Proposed local crawl maximum: **3 HTML pages/site**, depth **1**, **20-second** deadline, four redirects/request, 1 MB/response, one request per host at a time with at least 0.5 seconds between starts and any longer robots crawl delay. This means at most 72 page fetches plus up to 24 robots requests for the local adapter, excluding redirect hops. Redirects can increase total HTTP requests; the final request ceiling must be included in the approval list. Stop on denial and do not retry around it.
5. Capture all sites in a one-day window, then freeze bytes and metadata. Rerun extraction only from frozen captures. Keep public-site captures local and tenant isolated. Retain them for the documented seven-day default unless the approved evaluation records a different retention period.

The four labels should be customer-defined for a single named plan per site. If a plan name or term is not explicitly supported in the captured source, label it missing or conflicting. Numeric labels retain currency and period. The final per-site schema and plan name belong in the target inventory before approval.

## Independent labeling and review

- Two people independently label each held-out site from frozen captures **without viewing machine values**. Use `labels.template.json` and save a completed `labels.json` containing state, exact expected value, unit/currency, source URL, SHA-256 and supporting excerpt. Record seconds spent on every field. Disagreements are adjudicated and both originals preserved.
- A separate reviewer uses `/review` to judge the machine output (`correct`, `wrong`, `unsupported`, `conflicting`, `uncertain`), enter corrections and time. This measures review burden. These verdicts are not substituted for blind expected labels.
- Count a supported-looking value as wrong if its source URL, fetch timestamp, hash, locator, label or parsed value cannot be validated against the corresponding frozen capture.
- Record per-site failures: discovery, tables, metadata, visibility, encoding, JavaScript shell, unsupported schema, access policy, numeric context, and reviewer effort. Keep example failures, with customer-sensitive details redacted before external sharing.

## Offline bundle and replay commands

After an **approved** local job exists, export its captures and a blind-label template without another network request:

```powershell
python -m benchmark.export_local --db data/verified_extraction.sqlite3 --tenant TENANT --job-id JOB_ID --site-id SITE_ID --split development --category pricing --permission-note "approval record ID" --output benchmark/local/SITE_ID
```

The bundle contains `manifest.json`, byte captures (`.bin`), inert decoded text (`.txt`) and `labels.template.json`. An independent reviewer fills `labels.json`; the loader rejects missing labels, hash mismatches and evidence absent from its capture. Run local replay with:

```powershell
python -m benchmark.replay --bundles benchmark/local --split development --adapter local --output benchmark/local/development-results.json
python -m benchmark.replay --bundles benchmark/local --split held_out --adapter local --output benchmark/local/held-out-results.json
```

The output includes raw per-site result envelopes, expected labels, evidence checks, metric denominators, category summaries, examples of errors, and a deterministic site bootstrap interval. Captures and labels stay local and are ignored by Git. The interval is descriptive for this small sample, not a calibrated guarantee. Offline replay latency excludes live network latency; the original job envelope records observed end-to-end time separately.

## Metrics and decision rule

Report field-level precision and recall, verified evidence validity, abstention, conflict detection, access/fetch failures, elapsed time, review minutes per correct accepted field, and cost per accepted field **when measured**. Report numerators, denominators, by-site and by-category results. A verified field with invalid evidence is incorrect. Do not infer a cost from the current placeholder `0.0` internal cost; instrument compute and provider spend first.

This 24-site evaluation is an exploratory gate. A **conditional go** to a larger controlled pilot requires independently checked evidence, no critical source-binding or access-policy failures, useful accepted-field recall on held-out sites, reviewer time below the customer's manual baseline, and an agreed cost ceiling. Define numerical targets with the customer before reviewing held-out results. A **no-go or redesign** follows if wrong accepted values, low coverage, or review effort erase the benefit. Synthetic results cannot satisfy this gate.

## Equivalent alternative tests

For Firecrawl, Zyte, Diffbot and a crawler-plus-model baseline, use the same frozen task list, schema, site split and scoring rubric. Where a provider accepts frozen HTML, run a capture-locked extraction comparison. For a separate end-to-end comparison, obtain approval for each provider's account, exact sites, requests, maximum spend and data transfer; log actual calls, returned source references, charges, limits, latency and manual review minutes. Record unavoidable differences in crawl or rendering limits. No adapter is configured or benchmarked yet; unconfigured adapters must not emit performance scores.

## Approval gate and paid pilot

Before any live crawl or provider call, provide the user with the filled target CSV, access basis, per-site schema and plan, expected requests including redirects, date window, accounts, provider data destinations, maximum charges and impact. Approval must cover that concrete scope. Before public deployment, separately design authentication, TLS, a durable queue, tenant quotas, request and spend limits, encrypted storage/backups, retention enforcement, monitoring and controlled egress. The current local MVP is not ready for public deployment.

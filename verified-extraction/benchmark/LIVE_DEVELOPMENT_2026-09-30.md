# Live smoke and development run — 30 September 2026

The user authorized the separate IANA smoke check and the 12 development hosts in the frozen 24-site inventory for 30 September 2026. The source instruction and SHA-256 are recorded in ignored `benchmark/local/approval_record.json`. This authorization replaced the proposed 15 October window **for these two scopes only**. The 12 held-out product pages were not captured; the analyst hourly rate and threshold confirmation are still pending. After provisional source review, the user explicitly approved deletion of the ignored live captures. `benchmark/local/development/` and `benchmark/local/iana_smoke/` were removed on 30 September 2026; the local live capture database was already absent at cleanup. Independent labels cannot now be produced from those bytes without a newly approved capture. No account or paid provider was used.

## IANA smoke

The single run started at 08:45:57 UTC and ended at 08:46:00 UTC. `https://www.iana.org/domains/root/db/com.html` and its robots policy used **2 GETs of the 10 allowed**, with no redirect or access error. One 10,756-byte HTML response was frozen; its SHA-256 is `215c0303dcc4f74c1765c27eaacf359c334e1285532a2ba4abd8e49b4e38e6a5`. The raw bytes independently matched that digest. The job took 2.5062 seconds and returned `partial`: WHOIS Server, RDAP Server, and Email were all `missing`, with no accepted claim. The visible page contains those terms, but several labels and values share a block; the current parser did not bind them. This is a connectivity and access check with an extraction miss, not a customer-task score.

## Development hosts

The run started at 08:50:26 UTC and ended at 08:54:01 UTC. Every site was attempted once. The preflight and crawl each enforced the same hostname, robots policy, and a combined ceiling of 20 GETs/site. No denied or failed site was retried or replaced. Four sites yielded frozen three-page jobs, five stopped after preflight, and three hit a whole-job deadline. The known request count is **42**. The hard worker did not return a request count on three deadline paths, so the exact total is unknown; enforced ceilings bound it to **42–88**, below the authorized 240. This count gap limits access auditing and should be closed before a full evaluation.

| Site | Outcome | GETs started | Result |
| --- | --- | ---: | --- |
| Airtable | Out-of-scope redirect blocked during preflight | 2 | No product crawl |
| Atlassian Jira | Response over 1 MB cap | 2 | No product crawl |
| ClickUp | Captured | 6 | 6 missing fields |
| Dropbox | Preflight deadline | Unknown, at most 10 | No product crawl |
| Freshdesk | Response over 1 MB cap | 2 | No product crawl |
| GitLab | Captured | 6 | 6 missing fields |
| Intercom | Response over 1 MB cap | 2 | No product crawl |
| Miro | Captured | 6 | 6 missing fields |
| New Relic | Product job deadline | 2 + unknown, at most 20 total | No completed product result |
| Salesforce | Captured, one same-host redirect in preflight | 9 | 6 missing fields |
| Smartsheet | Product job deadline | 2 + unknown, at most 20 total | No completed product result |
| Zendesk | Response over 1 MB cap | 3 | No product crawl |

Across the four completed jobs, the machine accepted **0 of 24 requested fields**: 24 abstentions, no wrong accepted claims, and no reported conflicts. That zero is not precision or evidence-validity proof because there are no accepted values. Live product-job elapsed times were ClickUp 17.9997 s, GitLab 14.8357 s, Miro 15.1958 s, and Salesforce 16.2541 s. The other eight sites have no comparable completed-job latency.

Inspection of the frozen **seed-page visible text** found obvious missed plan and price context, without assigning independent ground truth. ClickUp's Unlimited card says `$7 Per user/month, billed yearly` and `$10 Per user/month, billed monthly` (capture `270c0b89305c4bf4383487d0bf263b7df9f761a582ff1af13e05739508fa7dd2`). GitLab's Premium area shows `$ 29 per user/month, billed annually` (capture `d1b18aa92d3464577513dad3e6f4ac1f257cb825eb96e0198ca58e3ba561b7b8`). Miro's Starter area shows `$ 8 /month per member, billed yearly` (capture `f43ec2bfd572380f27d62ddfdbd7c3b5f34841dd510dc86f7e1edbac605dde4b`). Salesforce's Starter Suite area shows `$ 25 USD/User/Month` and a monthly or annual billing note (capture `3d7b28f85a396ba8f7884f1a20d6363d6a5b4bf916314df807ca0709bc05d011`). Currency must not be inferred from `$` for the independent vendor-row task. These examples explain the current parser's limited recall on plan cards; they are not full six-field reviewed rows.

The export tool created blind-label, manual-baseline, and corrected-row templates for the four frozen jobs. Independent development labels and paired manual and assisted review have **not** been completed. Reviewer time, corrected-row error, customer value, compute cost, and labor cost are therefore unmeasured. Provider charges were USD 0. The held-out evaluation and pilot decision remain **incomplete**. Do not use these development pages as held-out evidence or tune against future held-out pages.

## Offline extraction repair on the frozen captures

The 30 September live outputs remain unchanged. A later offline replay of those same byte captures, with the preselected plan passed as `target_plan`, produced the following **unlabeled development regression**:

| Site | Original accepted / 6 | Repair accepted / 6 | Repair abstentions / 6 | Source checks |
| --- | ---: | ---: | ---: | ---: |
| ClickUp / Unlimited | 0 | 2 (plan, yearly billing) | 4 | 2/2 |
| GitLab / Premium | 0 | 2 (plan, annual billing) | 4 | 2/2 |
| Miro / Starter | 0 | 2 (plan, yearly billing) | 4 | 2/2 |
| Salesforce / Starter Suite | 0 | 1 (plan) | 5 | 1/1 |
| **Total** | **0/24** | **7/24** | **17/24** | **7/7** |

There were no emitted conflicts or known wrong-plan accepted claims in this replay. The seven accepted claims passed the extractor's URL, fetch time, hash, DOM, excerpt, and value recheck. This is **not** a real-site accuracy or recall measurement: no independent reviewer has validated all 24 fields. Provisional source-only packets were prepared locally, explicitly marked `provisional`, and their accepted citations were checked with the frozen-label source validator. They were not put into the blind-label loader or scored as independent truth. A reviewer still needs to decide the six fields for each plan without seeing machine output.

The original parser only recognized a leading exact `Label: value` form or a top-level JSON-LD key. All four seed pages instead place the plan heading and price in separate nodes inside a card, with some comparison rows in tables. The repair recognizes a local named-plan card or one unspanned table column. It still leaves the prices unresolved in these four captures: ClickUp, GitLab, and Miro show `$` without explicit currency in the local price node; Salesforce contains multiple regional currencies and says billed monthly **or** annually. ClickUp's `$10` monthly variant is present in raw HTML under `aria-hidden=true`, so it cannot support a visible monthly label. GitLab's annual period is emitted from its local card, while the independent label validator currently rejects that card's broader scope because it also contains a “Let's talk” heading; independent adjudication remains necessary.

The five response-size stops prove only that those responses exceeded the existing 1 MB cap; no full page bytes were retained for them. Dropbox timed out in preflight. New Relic and Smartsheet completed preflight page fetches but their product jobs timed out; the record does not distinguish slow network from later extraction work. The original runner repeated robots and seed-page GETs for preflight and product crawl. A future approved run can use one bounded job whose first robots and seed requests serve as preflight, without relaxing the 20-GET, 20-second, 1 MB, or hostname controls. The new durable GET-start counter reports counts on future hard deadlines and worker failures; it cannot reconstruct the three historical counts.

A mocked local Chrome browser run verified submission, reopening, evidence text, timed review, saving, history, export, an unauthorized API response, a 390-pixel layout without horizontal overflow, and keyboard Tab focus. The first attempt could not reach Chrome's loopback debugging port in the restricted sandbox; the same harness passed with local loopback access. It did not crawl any live site. Held-out evaluation and the pilot decision remain pending.

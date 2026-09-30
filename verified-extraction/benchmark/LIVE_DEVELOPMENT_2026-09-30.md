# Live smoke and development run — 30 September 2026

The user authorized the separate IANA smoke check and the 12 development hosts in the frozen 24-site inventory for 30 September 2026. The source instruction and SHA-256 are recorded in ignored `benchmark/local/approval_record.json`. This authorization replaced the proposed 15 October window **for these two scopes only**. The 12 held-out product pages were not captured; the analyst hourly rate and threshold confirmation are still pending. All captures, result envelopes, and review templates are local under ignored `benchmark/local/`; they must be deleted by **7 October 2026** under the seven-day retention policy. No account or paid provider was used.

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

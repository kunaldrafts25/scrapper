# One-link evaluation proposal — approval pending

This is a **new** 24-page proposal for the automatic one-link result. The 30 September approval and development captures belonged to the earlier procurement task and do not authorize this inventory. The URLs below are offline hypotheses; access, robots status, redirects, and page content have **not** been checked. No request may be sent from this proposal alone.

The machine-readable, frozen selection is [one_link_targets.csv](one_link_targets.csv). There are three development and three held-out hostnames for each of article, organization/service, product, and SaaS pricing. No hostname repeats. Pages that redirect outside their listed hostname, deny robots access, exceed size limits, or fail are recorded as outcomes and are not replaced.

| Split | Type | Exact seed URL | Allowed host |
| --- | --- | --- | --- |
| Development | Article | https://www.iana.org/domains/reserved | www.iana.org |
| Development | Article | https://developer.mozilla.org/en-US/docs/Web/HTML | developer.mozilla.org |
| Development | Article | https://docs.djangoproject.com/en/stable/intro/overview/ | docs.djangoproject.com |
| Development | Organization/service | https://www.python.org/about/ | www.python.org |
| Development | Organization/service | https://www.mozilla.org/en-US/about/ | www.mozilla.org |
| Development | Organization/service | https://www.redhat.com/en/about | www.redhat.com |
| Development | Product | https://www.raspberrypi.com/products/raspberry-pi-5/ | www.raspberrypi.com |
| Development | Product | https://www.atlassian.com/software/jira | www.atlassian.com |
| Development | Product | https://www.figma.com/figjam/ | www.figma.com |
| Development | SaaS pricing | https://clickup.com/pricing | clickup.com |
| Development | SaaS pricing | https://about.gitlab.com/pricing/ | about.gitlab.com |
| Development | SaaS pricing | https://miro.com/pricing/ | miro.com |
| Held out | Article | https://www.w3.org/Provider/Style/URI | www.w3.org |
| Held out | Article | https://www.gnu.org/philosophy/free-sw.html | www.gnu.org |
| Held out | Article | https://www.rfc-editor.org/rfc/rfc2606.html | www.rfc-editor.org |
| Held out | Organization/service | https://www.apache.org/foundation/ | www.apache.org |
| Held out | Organization/service | https://wikimediafoundation.org/about/ | wikimediafoundation.org |
| Held out | Organization/service | https://www.linuxfoundation.org/about/ | www.linuxfoundation.org |
| Held out | Product | https://www.arduino.cc/en/hardware | www.arduino.cc |
| Held out | Product | https://www.box.com/cloud-storage | www.box.com |
| Held out | Product | https://www.smartsheet.com/platform | www.smartsheet.com |
| Held out | SaaS pricing | https://asana.com/pricing | asana.com |
| Held out | SaaS pricing | https://github.com/pricing | github.com |
| Held out | SaaS pricing | https://www.notion.com/pricing | www.notion.com |

## Exact proposed scope

- **Window:** 15 October 2026, 09:00–11:00 Asia/Kolkata. This is proposed, not approved. An alternative window requires a new explicit record before requests.
- **Traffic:** at most **10 HTTP GET starts per listed hostname and 240 overall**, including robots checks and redirects. One seed HTML page per site, depth zero, at most four redirects per request, 1 MB per response, 20 seconds per site. The existing one-host lease serializes in-flight requests and enforces at least 0.5 seconds between starts or a longer robots delay. Each host receives read-only GETs only. Denial, unavailable robots policy, private address, or out-of-scope redirect stops that site.
- **Accounts and destinations:** no login, account, proxy, browser renderer, or paid provider. Frozen response bytes, metadata, results, and reviewer records remain under ignored `benchmark/local/one_link/` and the local SQLite database at `benchmark/local/one_link.sqlite3`; no remote data destination. The evaluation operator deletes those artifacts within seven days of capture. SQLite row deletion does not securely erase free pages, WAL files, backups, or disk snapshots.
- **Charges and impact:** expected provider charge USD 0, maximum provider spend USD 0. Local compute and analyst labor must be measured separately. At most 10 read-only GETs per host and 240 overall; no assets or script execution. No site receives a retry or substitution without a new approval.
- **Approval record:** must name these exact 24 URLs and hosts, limits, window, destinations, retention, accounts, spend, and impact. It must also record the approver and timestamp. Every row currently has `PENDING` approval and `NOT_CHECKED` robots status. `python -m benchmark.validate_one_link_targets --mode execution` must reject until a separately recorded approval and bounded access check resolve them. The prompt requesting implementation is not approval.

## Preregistered scoring

Independent reviewers label useful facts supported by each frozen page **before seeing machine output**. Two held-out reviewers work separately; disagreements retain both originals, identities, reasons, time, and an adjudicated final decision. An agent-made label is provisional and cannot count as independent ground truth. The unit is a page-specific fact with entity/plan, exact value or text, currency, unit, and billing period where applicable. Machine-assisted verdicts and corrected rows remain separate.

Report by page and in aggregate: accepted-claim precision (correct accepted / all accepted), recall (correct useful facts / independently labeled useful facts), wrong-entity and wrong-plan accepted counts, evidence validity (source-bound accepted / accepted checked), abstentions, pages with at least one useful supported result / accessible pages, static-page p50 and p95 elapsed time, GET starts, access failures and violations, reviewer minutes, compute, labor, and provider costs. Show raw denominators and wrong accepted examples. Apply the following held-out targets only with complete independent labels: **100% evidence validity, zero wrong entity/plan accepted, at least 95% accepted precision, at least 60% useful-fact recall, and useful results on at least 80% of accessible pages**. Missing labels, approvals, latency, or cost means **incomplete**, not pass or go. Development captures may guide fixes; freeze code before held-out requests and never tune on held-out pages.

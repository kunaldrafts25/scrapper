# Exact proposed real-site scope — approval pending

This is a proposal, not authorization. Every site currently has `access_basis=UNVERIFIED_PUBLIC_PAGE`, `robots_status=NOT_CHECKED`, and `approval_record=PENDING` in `candidate_targets.csv`. The URLs and plans are unverified offline hypotheses. The split was fixed before scraper output. Rejected sites remain in `rejection_log.csv`; none has been substituted.

| Split | Site / named plan | Exact seed URL | Only allowed hostname | Max GETs |
| --- | --- | --- | --- | ---: |
| Development | Airtable / Team | https://www.airtable.com/pricing | www.airtable.com | 20 |
| Held out | Asana / Starter | https://asana.com/pricing | asana.com | 20 |
| Development | Atlassian Jira / Standard | https://www.atlassian.com/software/jira/pricing | www.atlassian.com | 20 |
| Held out | Box / Business | https://www.box.com/pricing | www.box.com | 20 |
| Development | ClickUp / Unlimited | https://clickup.com/pricing | clickup.com | 20 |
| Held out | Datadog / Pro | https://www.datadoghq.com/pricing/ | www.datadoghq.com | 20 |
| Development | Dropbox / Business | https://www.dropbox.com/business/plans-comparison | www.dropbox.com | 20 |
| Held out | Figma / Professional | https://www.figma.com/pricing/ | www.figma.com | 20 |
| Development | Freshdesk / Growth | https://www.freshworks.com/freshdesk/pricing/ | www.freshworks.com | 20 |
| Held out | GitHub / Team | https://github.com/pricing | github.com | 20 |
| Development | GitLab / Premium | https://about.gitlab.com/pricing/ | about.gitlab.com | 20 |
| Held out | HubSpot / Starter | https://www.hubspot.com/pricing | www.hubspot.com | 20 |
| Development | Intercom / Essential | https://www.intercom.com/pricing | www.intercom.com | 20 |
| Held out | Linear / Standard | https://linear.app/pricing | linear.app | 20 |
| Development | Miro / Starter | https://miro.com/pricing/ | miro.com | 20 |
| Held out | monday.com / Basic | https://monday.com/pricing/ | monday.com | 20 |
| Development | New Relic / Core | https://newrelic.com/pricing | newrelic.com | 20 |
| Held out | Notion / Plus | https://www.notion.com/pricing | www.notion.com | 20 |
| Development | Salesforce / Starter Suite | https://www.salesforce.com/small-business/starter/ | www.salesforce.com | 20 |
| Held out | Slack / Pro | https://slack.com/pricing | slack.com | 20 |
| Development | Smartsheet / Pro | https://www.smartsheet.com/pricing | www.smartsheet.com | 20 |
| Held out | Trello / Standard | https://trello.com/pricing | trello.com | 20 |
| Development | Zendesk / Suite Team | https://www.zendesk.com/pricing/ | www.zendesk.com | 20 |
| Held out | Zoom / Pro | https://zoom.us/pricing | zoom.us | 20 |

**Shared scope and impact:** 480 HTTP GETs maximum overall, including robots checks and redirects; 20 GETs/site, three HTML pages/site, depth one, four redirects/request, 1 MB/response, and a 20-second job deadline. Each site sees at most 20 read-only GETs. Hosts are serialized with at least 0.5 seconds between request starts or a longer robots delay. A robots denial, unavailable policy, or out-of-scope redirect stops the site. No retry or replacement is proposed.

**Window, accounts, data and charges:** Proposed date 2026-10-15 only. Captures and results stay local and tenant isolated in `benchmark/local/` and local SQLite storage, with seven-day retention. No account or paid provider is proposed. Maximum provider spend and expected provider charges are USD 0/site and USD 0 overall. Local compute and analyst labor remain unpriced until the customer agrees to a labor rate and measured costs are recorded.

**Separate smoke check, also pending approval:** `https://www.iana.org/domains/root/db/com.html`, hostname `www.iana.org` only, at most 10 GETs including robots and redirects, one page, depth zero, 20-second deadline, no account or provider spend, local storage, seven-day retention. Proposed window: 2026-10-15 09:00–10:00 Asia/Kolkata. See `ONE_SITE_TEST_PLAN.md`. This check is outside the 480-GET evaluation limit.

Approval must cover the exact scope and the preregistered thresholds and labor rate. After approval, bounded robots/access preflight must be counted within each site's 20-GET ceiling, reducing the remaining product-job budget accordingly. Denied sites remain outcomes. Execution validation requires the approval record and allowed robots status before crawling permitted targets.

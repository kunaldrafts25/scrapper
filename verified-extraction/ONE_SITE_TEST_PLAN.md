# One-site live smoke test ? completed 30 September 2026

The user authorized this exact separate scope for 30 September 2026. The run used 2 of 10 GETs, captured one HTML page, and abstained on all three fields. See [the live report](benchmark/LIVE_DEVELOPMENT_2026-09-30.md). The original proposed window and terms below remain as the preregistered plan.


This is a bounded functional check, not part of the 24-site customer evaluation. It cannot establish live-site accuracy, recall, or commercial value.

- **Exact target:** `https://www.iana.org/domains/root/db/com.html` on `www.iana.org` only. IANA publicly publishes the `.COM` delegation record, including `WHOIS Server`, `RDAP Server`, and `Email` labels. The exact request is `benchmark/one_site_request.json`.
- **Access:** public, unauthenticated HTML only. The product first fetches `https://www.iana.org/robots.txt` and stops if it is unavailable or disallows the page. It follows no out-of-scope redirect. No login, CAPTCHA, proxy, or access workaround.
- **Volume:** one attempted page, depth zero, no hints, one robots check, 20-second whole-job deadline, 1 MB response cap, and an enforced 10-GET request ceiling. Normally two HTTP GETs. Each of robots and page requests can follow at most four redirects, so the absolute ceiling is ten GETs on the allowed hostname. Stop after this single job; no automatic retry.
- **Accounts and charges:** none; no paid providers, browser service, or model API. Expected provider charge: USD 0. Network, CPU, and local disk usage are unmetered, so no total resource-cost claim is made.
- **Proposed time window:** 2026-10-15, 09:00–10:00 Asia/Kolkata, subject to explicit approval and a fresh robots check by the product at run time.
- **Impact:** up to ten read-only GETs in 20 seconds to `www.iana.org`, with one leased in-flight request at a time and at least 0.5 seconds between actual starts or any longer robots crawl delay. Captured public bytes and result stay in local `benchmark/local/`, which is ignored by Git. Delete them after review or the documented seven-day retention period.
- **Outcome to inspect:** HTTP/robots result, page timestamp and hash, each field state/value/evidence, raw capture binding, elapsed time, and any abstention or conflict. Compare accepted values manually with the frozen source. Do not treat a missing or blocked field as a passing extraction claim.

The approved smoke run is recorded in the linked report above. It was separate from the vendor evaluation.

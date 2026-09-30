import json

from verified_extraction.automatic import build_automatic_result
from verified_extraction.fetch import Page
from verified_extraction.models import JobRequest
from verified_extraction.security import FetchError
from verified_extraction.service import run_job


def result(html):
    page = Page("https://example.org/pricing", html, "2026-09-30T00:00:00Z", [])
    return build_automatic_result([page], [], False)


def facts(data, group, key):
    return [item for item in data["facts"] if item["group"] == group and item["key"] == key]


def test_article_graph_is_anchored_and_hidden_text_is_excluded():
    graph = {"@graph": [
        {"@type": "NewsArticle", "headline": "A useful guide", "description": "Read the full guide.",
         "url": "https://example.org/pricing"},
        {"@type": "Product", "name": "Unrelated", "description": "Wrong entity",
         "url": "https://example.org/pricing"}]}
    html = ("<title>A useful guide</title><h1>A useful guide</h1>"
            "<p hidden>Support: Phone</p><p>Support: Email</p>"
            "<script type='application/ld+json'>" + json.dumps(graph) + "</script>")
    data = result(html)
    assert data["page_type"]["value"] == "article"
    assert facts(data, "Article", "headline")[0]["value"] == "A useful guide"
    assert not any(item.get("value") == "Wrong entity" for item in data["facts"])
    assert facts(data, "Details", "detail:support")[0]["value"] == "Email"
    assert all("Phone" not in str(item["evidence"]) for item in data["facts"])


def test_two_plan_cards_keep_prices_and_billing_separate():
    data = result("""<title>Plans</title><h1>Plans</h1>
      <article><h2>Team</h2><p>Price: USD 29 per user/month, billed annually</p></article>
      <article><h2>Business</h2><p>Price: EUR 59 per user/month, billed monthly</p></article>""")
    assert data["page_type"]["value"] == "saas_pricing"
    team = facts(data, "Plan: Team", "listed_price")[0]
    business = facts(data, "Plan: Business", "listed_price")[0]
    assert (team["value"], team["currency"], team["billing_period"]) == (29, "USD", "year")
    assert (business["value"], business["currency"], business["billing_period"]) == (59, "EUR", "month")
    assert team["evidence"][0]["snapshot_hash"]


def test_conflict_and_missing_are_explicit():
    data = result("<title>One</title><title>Two</title><h1>Heading</h1>")
    assert facts(data, "Page", "page_title")[0]["state"] == "ambiguous"
    assert facts(data, "Page", "page_title")[0]["value"] is None
    assert facts(data, "Page", "page_description")[0]["state"] == "missing"


def test_blocked_without_capture_has_no_claims():
    data = build_automatic_result([], [{"code": "ROBOTS_DENIED"}], True)
    assert data["overview"]["state"] == "blocked"
    assert data["facts"] == []


def test_product_offers_keep_variants_and_currency():
    structured = {"@type": "Product", "name": "Desk", "url": "https://example.org/pricing",
                  "offers": [{"name": "Small", "price": "19.50", "priceCurrency": "USD"},
                             {"name": "Large", "price": 29, "priceCurrency": "EUR"}]}
    data = result("<title>Desk</title><h1>Desk</h1><script type='application/ld+json'>" +
                  json.dumps(structured) + "</script>")
    assert data["page_type"]["value"] == "product"
    assert facts(data, "Offer: Small", "price")[0]["currency"] == "USD"
    assert facts(data, "Offer: Large", "price")[0]["currency"] == "EUR"


def test_capture_html_must_match_retained_bytes():
    page = Page("https://example.org/pricing", "<title>Forged</title>", "now", [],
                raw=b"<title>Original</title>")
    data = build_automatic_result([page], [], False)
    assert data["facts"] == []
    assert data["errors"][0]["code"] == "CAPTURE_DECODE_MISMATCH"


def test_latin1_capture_has_exact_source_evidence():
    html = "<title>Café guide</title><h1>Café guide</h1>"
    page = Page("https://example.org/pricing", html, "now", [], html.encode("latin-1"),
                "iso8859-1", 0, "text/html; charset=iso-8859-1")
    data = build_automatic_result([page], [], False)
    assert facts(data, "Page", "page_title")[0]["value"] == "Café guide"
    assert data["facts"][0]["evidence"][0]["snapshot_hash"]


def test_robots_redirect_and_deadline_are_explicit_outcomes():
    request = JobRequest(url="https://example.org/pricing", schema={"type": "object", "properties": {
        "page_title": {"type": "string"}, "main_heading": {"type": "string"},
        "page_description": {"type": "string"}}}, automatic=True, idempotency_key="access")
    for code, state in (("ROBOTS_DENIED", "blocked"), ("OUT_OF_SCOPE", "blocked"),
                        ("DEADLINE", "missing")):
        class Denied:
            def fetch(self, _url):
                raise FetchError(code, code)
        result, captures = run_job(request, Denied())
        assert result.status == "failed" and not captures
        assert result.automatic_result["overview"]["state"] == state
        assert result.automatic_result["errors"][0]["code"] == code

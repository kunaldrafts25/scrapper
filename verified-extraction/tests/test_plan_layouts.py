from verified_extraction.extract import extract_fields, verify_candidate
from verified_extraction.fetch import Page


SCHEMA = {
    "named_plan": {"type": "string", "title": "Plan"},
    "listed_price": {"type": "number", "title": "Price"},
    "currency": {"type": "string", "title": "Currency"},
    "billing_period": {"type": "string", "title": "Billing period"},
    "usage_or_seat_limit": {"type": "string", "title": "Usage limit"},
    "support_channel": {"type": "string", "title": "Support"},
}


def page(html):
    return Page("https://example.org/pricing", html, "2026-09-30T00:00:00Z", [])


def test_named_card_keeps_price_period_and_support_on_one_plan():
    source = page("""<article><h2>Team</h2><div>Price: USD 29.50 per user/month, billed annually</div>
        <p>Support: Email</p></article><article><h2>Business</h2>
        <div>Price: USD 59 per user/month, billed annually</div><p>Support: Phone</p></article>""")
    fields = extract_fields([source], SCHEMA, False, "Team")
    assert fields["named_plan"].value == "Team"
    assert fields["listed_price"].state == "verified"
    assert (fields["listed_price"].value, fields["listed_price"].unit,
            fields["listed_price"].currency, fields["listed_price"].billing_period,
            fields["listed_price"].numeric_encoding) == ("29.5", "month", "USD", "year", "decimal-string")
    assert fields["currency"].value == "USD"
    assert fields["billing_period"].value == "year"
    assert fields["support_channel"].value == "Email"
    assert all("Business" not in evidence.excerpt for field in fields.values() for evidence in field.evidence)
    for name, field in fields.items():
        if field.state == "verified":
            from verified_extraction.models import Candidate
            candidate = Candidate(value=field.value, unit=field.unit, currency=field.currency,
                billing_period=field.billing_period, numeric_encoding=field.numeric_encoding,
                value_type=SCHEMA[name]["type"], evidence=field.evidence[0])
            assert verify_candidate(candidate, source)


def test_same_plan_two_billing_prices_conflict_and_dollar_only_abstains():
    source = page("""<article><h2>Team</h2><div>USD 29 per user/month, billed annually</div>
        <div>USD 39 per user/month, billed monthly</div></article>""")
    fields = extract_fields([source], SCHEMA, False, "Team")
    assert fields["listed_price"].state == "conflicting"
    assert {(item.value, item.billing_period) for item in fields["listed_price"].candidates} == {(29, "year"), (39, "month")}
    assert fields["billing_period"].state == "conflicting"
    dollar = page("<article><h2>Team</h2><div>$29 per user/month, billed annually</div></article>")
    assert extract_fields([dollar], SCHEMA, False, "Team")["listed_price"].state == "missing"


def test_table_column_and_wrong_plan_are_kept_separate():
    source = page("""<table><tr><th>Plan</th><th>Team</th><th>Business</th></tr>
        <tr><th>Price</th><td>USD 29 per user/month, billed annually</td>
        <td>USD 59 per user/month, billed annually</td></tr></table>""")
    fields = extract_fields([source], SCHEMA, False, "Team")
    assert fields["listed_price"].value == 29
    assert fields["named_plan"].value == "Team"
    assert extract_fields([source], SCHEMA, False, "Business")["listed_price"].value == 59


def test_explicit_non_dollar_currency_symbols():
    for symbol, currency in [("€", "EUR"), ("£", "GBP"), ("₹", "INR")]:
        source = page(f"<article><h2>Team</h2><p>{symbol}29 per user/month, billed annually</p></article>")
        price = extract_fields([source], SCHEMA, False, "Team")["listed_price"]
        assert (price.state, price.value, price.currency, price.billing_period) == (
            "verified", 29, currency, "year")

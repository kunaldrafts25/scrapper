"""Conservative, extractive one-link result from a retained HTML capture.

Every displayed value is regenerated from its original Page before acceptance.
This is intentionally a bounded first pass, not open-ended semantic extraction.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from decimal import Decimal
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from bs4.element import Tag

from .extract import (_visible_text, dom_path, is_hidden, page_fact_candidates,
                      plan_candidates, snapshot_hash, verify_candidate)
from .fetch import Page, decode_html
from .models import Candidate

MAX_FACTS = 60
PLAN_SCHEMA = {
    "named_plan": {"type": "string", "title": "Plan"},
    "listed_price": {"type": "number", "title": "Price"},
    "currency": {"type": "string", "title": "Currency"},
    "billing_period": {"type": "string", "title": "Billing period"},
    "usage_or_seat_limit": {"type": "string", "title": "Usage limit"},
    "support_channel": {"type": "string", "title": "Support"},
}
PAGE_LABELS = {"page_title": "Title", "main_heading": "Main heading",
               "page_description": "Description", "site_name": "Site name"}
TYPE_MAP = {"article": "article", "newsarticle": "article", "blogposting": "article",
            "organization": "organization/service", "localbusiness": "organization/service",
            "service": "organization/service", "product": "product",
            "softwareapplication": "product", "webapplication": "product"}


def _scalar(value) -> str | None:
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
        return None
    text = " ".join(str(value).split())
    return text if 0 < len(text) <= 220 else None


def _structured_nodes(page: Page):
    soup = BeautifulSoup(page.html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        if is_hidden(script) or not script.string or len(script.string) > 30000:
            continue
        try:
            data = json.loads(script.string, parse_float=Decimal)
        except (ValueError, TypeError):
            continue
        roots = data if isinstance(data, list) else [data]
        for root_index, root in enumerate(roots):
            if not isinstance(root, dict):
                continue
            children = root.get("@graph") if isinstance(root.get("@graph"), list) else [root]
            for index, node in enumerate(children[:30]):
                if isinstance(node, dict):
                    yield script, (root_index, index), node


def _entity_types(node: dict) -> set[str]:
    raw = node.get("@type", [])
    return {str(item).rsplit("/", 1)[-1].casefold() for item in (raw if isinstance(raw, list) else [raw])}


def _visible_anchor(page: Page) -> set[str]:
    soup = BeautifulSoup(page.html, "html.parser")
    values = [*_visible_heads(soup)]
    for meta in soup.find_all("meta"):
        if str(meta.get("property", "")).casefold() == "og:title":
            values.append(str(meta.get("content", "")))
    return {" ".join(value.casefold().split()) for value in values if value}


def _visible_heads(soup: BeautifulSoup):
    for node in soup.find_all(["h1", "title"]):
        if node.name == "title" or not is_hidden(node):
            value = _visible_text(node) if node.name == "h1" else node.get_text(" ", strip=True)
            if value:
                yield value


def _main_entities(page: Page):
    anchors = _visible_anchor(page)
    path = urlsplit(page.url).path.rstrip("/") or "/"
    for script, pointer, node in _structured_nodes(page):
        types = _entity_types(node)
        if not types.intersection(TYPE_MAP):
            continue
        url = node.get("url") or node.get("mainEntityOfPage")
        if isinstance(url, dict):
            url = url.get("@id")
        if isinstance(url, str):
            parsed = urlsplit(url)
            if parsed.hostname and (parsed.hostname != urlsplit(page.url).hostname or
                                    (parsed.path.rstrip("/") or "/") != path):
                continue
        name = _scalar(node.get("headline")) or _scalar(node.get("name"))
        if not name or name.casefold() not in anchors:
            continue
        yield script, pointer, node, types


def _claim(page: Page, group: str, key: str, label: str, value: str, node: Tag,
           excerpt: str, suffix: str = "", *, currency=None, unit=None, billing_period=None,
           numeric_encoding=None):
    return {"group": group, "key": key, "label": label, "value": value,
            "currency": currency, "unit": unit, "billing_period": billing_period,
            "numeric_encoding": numeric_encoding,
            "evidence": {"source_url": page.url, "fetched_at": page.fetched_at,
                         "snapshot_hash": snapshot_hash(page), "locator": dom_path(node) + suffix,
                         "excerpt": excerpt, "raw_value": value}}


def _all_claims(page: Page):
    soup = BeautifulSoup(page.html, "html.parser")
    for key, label in PAGE_LABELS.items():
        for candidate in page_fact_candidates(page, key, {"type": "string"}):
            yield {"group": "Page", "key": key, "label": label, "value": candidate.value,
                   "currency": None, "unit": None, "billing_period": None, "numeric_encoding": None,
                   "evidence": candidate.evidence.model_dump()}
    for script, pointer, node, types in _main_entities(page):
        group = "Article" if types.intersection({"article", "newsarticle", "blogposting"}) else \
                "Product" if types.intersection({"product", "softwareapplication", "webapplication"}) else "Organization"
        for key, label in (("headline", "Headline"), ("name", "Name"),
                           ("description", "Description"), ("datePublished", "Published")):
            value = _scalar(node.get(key))
            if value:
                yield _claim(page, group, key, label, value, script, value,
                             f"::auto-json({pointer[0]},{pointer[1]},{key})")
        if group == "Product":
            offers = node.get("offers")
            for offer_index, offer in enumerate((offers if isinstance(offers, list) else [offers])[:12]):
                if not isinstance(offer, dict):
                    continue
                price = _scalar(offer.get("price"))
                currency = _scalar(offer.get("priceCurrency"))
                if not price or not currency or currency not in {"USD", "EUR", "GBP", "INR"}:
                    continue
                offer_name = _scalar(offer.get("name")) or f"Offer {offer_index + 1}"
                offer_group = "Offer: " + offer_name
                claim = _claim(page, offer_group, "price", "Listed price", price, script, price,
                               f"::auto-offer({pointer[0]},{pointer[1]},{offer_index})", currency=currency,
                               numeric_encoding="decimal-string" if "." in price else "integer-string")
                yield claim
    for node in soup.find_all("p"):
        if is_hidden(node) or node.find_parent(["aside", "nav", "footer"]):
            continue
        value = _visible_text(node)
        if 45 <= len(value) <= 240 and not re.match(r"^[\w /-]{2,45}:\s*\S", value):
            yield _claim(page, "Page", "overview_text", "On-page summary", value, node, value)
            break
    for node in soup.find_all(["p", "li", "dt", "tr"]):
        if is_hidden(node) or node.find_parent(["nav", "footer", "aside"]):
            continue
        if node.name == "dt":
            sibling = node.find_next_sibling("dd")
            if sibling is None or is_hidden(sibling):
                continue
            label, value = _visible_text(node), _visible_text(sibling)
            source_node = sibling
        elif node.name == "tr":
            cells = node.find_all(["th", "td"], recursive=False)
            if len(cells) != 2 or any(cell.has_attr("colspan") or cell.has_attr("rowspan") for cell in cells):
                continue
            label, value, source_node = _visible_text(cells[0]), _visible_text(cells[1]), cells[1]
        else:
            text = _visible_text(node)
            match = re.fullmatch(r"([^:]{2,40}):\s*(.{1,180})", text)
            if not match:
                continue
            label, value, source_node = match.group(1), match.group(2), node
        if not 2 <= len(label) <= 40 or not 1 <= len(value) <= 180:
            continue
        if label.casefold() in {"price", "cost", "rate"} and node.find_parent("article"):
            continue  # A plan-card price is only emitted in its own plan group.
        if any(node.find_parent(parent) for parent in ("script", "template")):
            continue
        key = "detail:" + re.sub(r"\W+", "_", label.casefold()).strip("_")
        if key == "detail:" or key in {"detail:cookie", "detail:privacy"}:
            continue
        yield _claim(page, "Details", key, label, value, source_node, _visible_text(source_node))
    # A plan is a separate entity. Only claims generated inside its local card or
    # one comparison-table column can be shown in that plan's group.
    names = []
    for heading in soup.find_all(["h2", "h3", "h4"]):
        if is_hidden(heading):
            continue
        name = _visible_text(heading)
        if 1 <= len(name) <= 60 and name not in names:
            names.append(name)
    for table in soup.find_all("table"):
        first = table.find("tr")
        if first:
            names.extend(_visible_text(cell) for cell in first.find_all(["th", "td"], recursive=False)[1:])
    for name in dict.fromkeys(names[:12]):
        if name.casefold() in {"pricing", "plans", "compare plans", "features", "faq",
                               "frequently asked questions", "contact sales", "let's talk"}:
            continue
        plan = plan_candidates(page, "named_plan", PLAN_SCHEMA["named_plan"], name, soup)
        if not plan:
            continue
        group = "Plan: " + name
        for key, spec in PLAN_SCHEMA.items():
            for candidate in plan if key == "named_plan" else plan_candidates(page, key, spec, name, soup):
                yield {"group": group, "key": key, "label": spec["title"], "value": candidate.value,
                       "currency": candidate.currency, "unit": candidate.unit,
                       "billing_period": candidate.billing_period, "numeric_encoding": candidate.numeric_encoding,
                       "evidence": candidate.evidence.model_dump()}


def _source_bound(claim: dict, page: Page, regenerated: set[tuple]) -> bool:
    ev = claim["evidence"]
    return (ev["source_url"] == page.url and ev["fetched_at"] == page.fetched_at and
            ev["snapshot_hash"] == snapshot_hash(page) and
            (claim["group"], claim["key"], str(claim["value"]), claim["currency"],
             claim["unit"], claim["billing_period"], claim.get("numeric_encoding"),
             ev["locator"], ev["excerpt"]) in regenerated)


def _page_type(page: Page, claims: list[dict]):
    types = set()
    for _, _, _, entity_types in _main_entities(page):
        types.update(TYPE_MAP[item] for item in entity_types if item in TYPE_MAP)
    if any(item["group"].startswith("Plan: ") for item in claims):
        types.add("saas_pricing")
    soup = BeautifulSoup(page.html, "html.parser")
    for meta in soup.find_all("meta"):
        if str(meta.get("property", "")).casefold() == "og:type":
            value = str(meta.get("content", "")).casefold()
            if value == "article":
                types.add("article")
            elif value == "product":
                types.add("product")
    if not types and soup.find("article") and soup.find("time"):
        types.add("article")
    if len(types) == 1:
        return {"value": next(iter(types)), "confidence": "source_signals", "alternatives": []}
    return {"value": "general", "confidence": "uncertain", "alternatives": sorted(types)}


def build_automatic_result(pages: list[Page], errors: list[dict], blocked: bool) -> dict:
    """Versioned automatic result; only the seed page contributes facts."""
    if not pages:
        state = "blocked" if blocked else "missing"
        return {"version": "1.0", "page_type": {"value": "general", "confidence": "uncertain", "alternatives": []},
                "overview": {"state": state, "text": None, "evidence": []}, "groups": [],
                "facts": [], "errors": errors, "fetched_at": None}
    page = pages[0]
    decoded, _, _ = decode_html(page.raw or b"", page.content_type)
    if decoded != page.html:
        return {"version": "1.0", "page_type": {"value": "general", "confidence": "uncertain", "alternatives": []},
                "overview": {"state": "blocked", "text": None, "evidence": []}, "groups": [],
                "facts": [], "errors": [*errors, {"code": "CAPTURE_DECODE_MISMATCH"}], "fetched_at": page.fetched_at}
    raw = list(_all_claims(page))[:250]
    # Independent regeneration from retained bytes, URL, and fetch metadata.
    replay = Page(page.url, page.html, page.fetched_at, page.redirects, page.raw,
                  page.encoding, page.decoding_errors, page.content_type)
    regenerated = {(x["group"], x["key"], str(x["value"]), x["currency"], x["unit"],
                    x["billing_period"], x.get("numeric_encoding"), x["evidence"]["locator"],
                    x["evidence"]["excerpt"]) for x in _all_claims(replay)}
    bound = [claim for claim in raw if _source_bound(claim, replay, regenerated)]
    # Recheck existing page-fact and plan claim semantics, including hidden and
    # wrong-plan exclusions, against the original bytes.
    safe = []
    for claim in bound:
        ev = claim["evidence"]
        if "::plan-scope(" in ev["locator"] or "::page-fact(" in ev["locator"]:
            from .models import Evidence
            kind = "number" if claim["key"] == "listed_price" else "string"
            candidate = Candidate(value=claim["value"], value_type=kind, currency=claim["currency"],
                unit=claim["unit"], billing_period=claim["billing_period"],
                numeric_encoding=claim.get("numeric_encoding"),
                evidence=Evidence.model_validate(ev))
            if not verify_candidate(candidate, replay):
                continue
        safe.append(claim)
    groups = defaultdict(lambda: defaultdict(list))
    for claim in safe:
        groups[claim["group"]][claim["key"]].append(claim)
    facts = []
    for group, by_key in groups.items():
        for key, items in by_key.items():
            variants = {}
            for item in items:
                token = (str(item["value"]), item["currency"], item["unit"], item["billing_period"])
                variants.setdefault(token, item)
            chosen = next(iter(variants.values()))
            facts.append({"group": group, "key": key, "label": chosen["label"],
                          "variant": group.split(": ", 1)[1] if group.startswith(("Plan: ", "Offer: ")) else None,
                          "state": "found_in_source" if len(variants) == 1 else "ambiguous",
                          "value": chosen["value"] if len(variants) == 1 else None,
                          "currency": chosen["currency"] if len(variants) == 1 else None,
                          "unit": chosen["unit"] if len(variants) == 1 else None,
                          "billing_period": chosen["billing_period"] if len(variants) == 1 else None,
                          "numeric_encoding": chosen.get("numeric_encoding") if len(variants) == 1 else None,
                          "evidence": [item["evidence"] for item in variants.values()],
                          "alternatives": [{"value": item["value"], "currency": item["currency"],
                                            "unit": item["unit"], "billing_period": item["billing_period"]}
                                           for item in variants.values()] if len(variants) > 1 else []})
    for key, label in PAGE_LABELS.items():
        if not any(item["key"] == key and item["group"] == "Page" for item in facts):
            facts.append({"group": "Page", "key": key, "label": label,
                          "state": "missing", "value": None, "evidence": [], "alternatives": []})
    order = {"page_title": 0, "main_heading": 1, "page_description": 2,
             "overview_text": 3, "site_name": 4, "named_plan": 5, "listed_price": 6}
    facts.sort(key=lambda item: (0 if item["group"] == "Page" else 1 if item["group"].startswith("Plan: ") else 2,
                                 order.get(item["key"], 20), item["label"]))
    seen = set()
    distinct = []
    for item in facts:
        token = (item["label"].casefold(), str(item["value"]).casefold())
        if (item["state"] == "found_in_source" and not item["group"].startswith(("Plan: ", "Offer: "))
                and token in seen):
            continue
        if item["state"] == "found_in_source":
            seen.add(token)
        distinct.append(item)
    facts = distinct[:MAX_FACTS]
    summary = next((item for item in facts if item["key"] in {"page_description", "overview_text", "description"}
                    and item["state"] == "found_in_source"), None)
    overview = {"state": "found_in_source" if summary else "missing",
                "text": summary["value"] if summary else None,
                "evidence": summary["evidence"] if summary else []}
    return {"version": "1.0", "page_type": _page_type(page, safe), "overview": overview,
            "groups": list(dict.fromkeys(item["group"] for item in facts)), "facts": facts,
            "errors": errors, "fetched_at": page.fetched_at}

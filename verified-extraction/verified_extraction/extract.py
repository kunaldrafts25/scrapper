from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
from bs4.element import Tag, NavigableString

from .fetch import Page
from .models import Candidate, Evidence, FieldResult
from .security import canonical_url, FetchError


def snapshot_hash(source: Page | bytes | str) -> str:
    raw = source.raw if isinstance(source, Page) else source.encode("utf-8") if isinstance(source, str) else source
    return hashlib.sha256(raw).hexdigest()


def dom_path(tag: Tag) -> str:
    parts = []
    while isinstance(tag, Tag) and tag.name != "[document]":
        index = 1 + sum(isinstance(s, Tag) and s.name == tag.name for s in tag.previous_siblings)
        parts.append(f"{tag.name}:nth-of-type({index})")
        tag = tag.parent
    return " > ".join(reversed(parts))


def is_hidden(tag: Tag) -> bool:
    for node in [tag, *tag.parents]:
        if not getattr(node, "attrs", None):
            continue
        if node.has_attr("hidden") or str(node.get("aria-hidden", "")).lower() == "true":
            return True
        style = re.sub(r"\s+", "", str(node.get("style", "")).lower())
        if any(re.search(p, style) for p in (r"(?:^|;)display:none(?:!important)?(?:;|$)",
                                         r"(?:^|;)visibility:hidden(?:!important)?(?:;|$)",
                                         r"(?:^|;)opacity:0(?:\.0+)?(?:!important)?(?:;|$)")):
            return True
    return False


def visible_blocks(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    def visible_text(node) -> str:
        parts = []
        def visit(current):
            if isinstance(current, NavigableString):
                value = str(current).strip()
                if value:
                    parts.append(value)
            elif isinstance(current, Tag) and current.name not in {"script", "style", "noscript", "template", "svg", "head"} and not is_hidden(current):
                for child in current.children:
                    visit(child)
        visit(node)
        return " ".join(parts)
    blocks = []
    for tag in soup.find_all(["p", "li", "h1", "h2", "h3", "tr", "dt", "dd"]):
        if is_hidden(tag):
            continue
        text = visible_text(tag)
        if text and len(text) <= 400:
            blocks.append((text, dom_path(tag)))
    return blocks


NUMBER = re.compile(r"^\s*(?P<currency>USD|EUR|GBP|INR|[$\u20ac\u00a3\u20b9])?\s*"
                    r"(?P<number>-?\d+(?:\.\d+)?)\s*"
                    r"(?P<unit>(?:/\s*|per\s+)?(?:month|year|day|user|seat|GB)|monthly|annually)?\s*"
                    r"(?P<suffix>USD|EUR|GBP|INR|dollars)?\s*$", re.I)
CURRENCY = {"$": "USD", "\u20ac": "EUR", "\u00a3": "GBP", "\u20b9": "INR", "dollars": "USD"}
MAX_SAFE_JSON_INTEGER = 2**53 - 1


def parse_scalar(raw: str, kind: str):
    raw = raw.strip()
    if kind == "string":
        return (raw, None, None) if 0 < len(raw) <= 200 else None
    if kind == "boolean":
        value = {"true": True, "yes": True, "false": False, "no": False}.get(raw.lower())
        return (value, None, None) if value is not None else None
    match = NUMBER.fullmatch(raw)
    if not match:
        return None
    digits = match.group("number")
    if len(digits.lstrip("-")) > 100:
        return None
    try:
        number = Decimal(digits)
    except InvalidOperation:
        return None
    if kind == "integer" and number != number.to_integral_value():
        return None
    prefix, suffix = match.group("currency"), match.group("suffix")
    prefix = CURRENCY.get(prefix, prefix.upper() if prefix else None)
    suffix = CURRENCY.get(suffix.lower(), suffix.upper()) if suffix else None
    if prefix and suffix and prefix != suffix:
        return None
    unit = match.group("unit")
    unit = unit.strip().lstrip("/").strip().lower() if unit else None
    if unit and unit.startswith("per "):
        unit = unit[4:]
    unit = {"monthly": "month", "annually": "year", "gb": "GB"}.get(unit, unit)
    if number == number.to_integral_value():
        integral = int(number)
        exact = integral if abs(integral) <= MAX_SAFE_JSON_INTEGER else str(integral)
    else:
        exact = format(number.normalize(), "f")
    return (exact, unit, prefix or suffix)


def numeric_encoding(value, kind: str) -> str | None:
    if kind not in {"number", "integer"}:
        return None
    return ("decimal-string" if "." in value else "integer-string") if isinstance(value, str) else "integer"


def label_pattern(label: str):
    return re.compile(r"^\s*" + re.escape(label) + r"\s*[:\-\u2013]\s*(.+?)\s*$", re.I)


def candidates_for(page: Page, name: str, spec: dict) -> list[Candidate]:
    label = spec.get("title") or name.replace("_", " ")
    results = []
    digest = snapshot_hash(page)
    for excerpt, locator in visible_blocks(page.html):
        match = label_pattern(label).match(excerpt)
        if not match:
            continue
        raw_value = match.group(1)
        parsed = parse_scalar(raw_value, spec["type"])
        if parsed is not None:
            value, unit, currency = parsed
            results.append(Candidate(value=value, unit=unit, currency=currency, value_type=spec["type"],
                numeric_encoding=numeric_encoding(value, spec["type"]),
                evidence=Evidence(source_url=page.url, fetched_at=page.fetched_at, excerpt=excerpt,
                    locator=locator, snapshot_hash=digest, label=label, raw_value=raw_value)))
    soup = BeautifulSoup(page.html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        if is_hidden(script):
            continue
        try:
            if not script.string or len(script.string) > 4000:
                continue
            data = json.loads(script.string, parse_float=Decimal)
        except (ValueError, TypeError):
            continue
        for index, node in enumerate(data if isinstance(data, list) else [data]):
            if not isinstance(node, dict) or name not in node or not isinstance(node[name], (str, int, float, bool, Decimal)):
                continue
            raw_value = str(node[name])
            parsed = parse_scalar(raw_value, spec["type"])
            if parsed is not None:
                value, unit, currency = parsed
                results.append(Candidate(value=value, unit=unit, currency=currency, value_type=spec["type"],
                    numeric_encoding=numeric_encoding(value, spec["type"]),
                    evidence=Evidence(source_url=page.url, fetched_at=page.fetched_at,
                        excerpt=script.string,
                        locator=f"{dom_path(script)}::item({index})::{name}", snapshot_hash=digest,
                        label=name, raw_value=raw_value)))
    return results


def verify_candidate(candidate: Candidate, source: Page | bytes | str) -> bool:
    if isinstance(source, bytes):
        raise TypeError("byte verification requires a Page with recorded encoding and source URL")
    if snapshot_hash(source) != candidate.evidence.snapshot_hash:
        return False
    if isinstance(source, Page) and (source.url != candidate.evidence.source_url or
                                     source.fetched_at != candidate.evidence.fetched_at):
        return False
    html = source.html if isinstance(source, Page) else source.decode("utf-8", "replace") if isinstance(source, bytes) else source
    ev = candidate.evidence
    if not ev.label or ev.raw_value is None or not candidate.value_type:
        return False
    if "::item(" in ev.locator:
        try:
            script_path, item_part, key = ev.locator.split("::", 2)
            index = int(item_part.removeprefix("item(").removesuffix(")"))
            soup = BeautifulSoup(html, "html.parser")
            script = next(tag for tag in soup.find_all("script", type="application/ld+json")
                          if dom_path(tag) == script_path and not is_hidden(tag))
            if script.string != ev.excerpt:
                return False
            data = json.loads(script.string or "", parse_float=Decimal)
            node = (data if isinstance(data, list) else [data])[index]
            if key != ev.label or not isinstance(node, dict) or key not in node:
                return False
            if str(node[key]) != ev.raw_value:
                return False
        except (ValueError, TypeError, IndexError, StopIteration):
            return False
    else:
        block = next((text for text, path in visible_blocks(html) if path == ev.locator), None)
        if block != ev.excerpt:
            return False
        match = label_pattern(ev.label).match(block)
        if not match or match.group(1) != ev.raw_value:
            return False
    return (parse_scalar(ev.raw_value, candidate.value_type) == (candidate.value, candidate.unit, candidate.currency)
            and candidate.numeric_encoding == numeric_encoding(candidate.value, candidate.value_type))


def source_node_for(html: str, locator: str) -> str | None:
    path = locator.split("::", 1)[0]
    soup = BeautifulSoup(html, "html.parser")
    tag = next((item for item in soup.find_all(True) if dom_path(item) == path), None)
    return str(tag)[:4000] if tag is not None else None


def extract_fields(pages: list[Page], properties: dict, blocked: bool) -> dict[str, FieldResult]:
    output = {}
    for name, spec in properties.items():
        raw = [candidate for page in pages for candidate in candidates_for(page, name, spec)]
        good = [candidate for candidate in raw if any(page.url == candidate.evidence.source_url
            and verify_candidate(candidate, page) for page in pages)]
        relevant = [c for c in good if (not spec.get("x-unit") or c.unit == spec["x-unit"])
                    and (not spec.get("x-currency") or c.currency == spec["x-currency"])]
        unique = {}
        for candidate in relevant:
            key = json.dumps([candidate.value, candidate.unit, candidate.currency], sort_keys=True)
            unique.setdefault(key, []).append(candidate)
        if len(unique) == 1:
            values = next(iter(unique.values()))
            first = values[0]
            output[name] = FieldResult(state="verified", value=first.value, unit=first.unit,
                currency=first.currency, numeric_encoding=first.numeric_encoding,
                evidence=[c.evidence for c in values])
        elif len(unique) > 1:
            output[name] = FieldResult(state="conflicting", candidates=[values[0] for values in unique.values()],
                reason="Distinct source-backed value, unit, or currency claims")
        elif raw or any(label_pattern(spec.get("title") or name.replace("_", " ")).match(text)
                        for page in pages for text, _ in visible_blocks(page.html)):
            output[name] = FieldResult(state="unverified", reason="Candidate unsupported, unit mismatch, or evidence check failed")
        elif blocked:
            output[name] = FieldResult(state="blocked", reason="Source access blocked")
        else:
            output[name] = FieldResult(state="missing", reason="No supported label/value found in fetched pages")
    return output


def links_for(page: Page, allowed: set[str], hints: list[str]) -> list[str]:
    soup = BeautifulSoup(page.html, "html.parser")
    found = []
    for link in soup.find_all("a", href=True):
        try:
            url = canonical_url(urljoin(page.url, link["href"]))
            if urlsplit(url).hostname in allowed and url not in found and not re.search(r"\.(pdf|jpg|png|css|js|zip)$", urlsplit(url).path, re.I):
                found.append(url)
        except FetchError:
            continue
    def score(url):
        low = url.lower()
        return sum(10 for hint in hints if hint.lower() in low) + sum(2 for word in ("pricing", "about", "plans", "faq") if word in low)
    return sorted(found, key=score, reverse=True)

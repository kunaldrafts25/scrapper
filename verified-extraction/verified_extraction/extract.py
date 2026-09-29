from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup

from .fetch import Page
from .models import Candidate, Evidence, FieldResult
from .security import canonical_url, FetchError


def snapshot_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def is_hidden(tag) -> bool:
    for node in [tag, *tag.parents]:
        if not getattr(node, "attrs", None):
            continue
        if node.has_attr("hidden") or str(node.get("aria-hidden", "")).lower() == "true":
            return True
        style = re.sub(r"\s+", "", str(node.get("style", "")).lower())
        if re.search(r"(?:^|;)display:none(?:!important)?(?:;|$)", style):
            return True
        if re.search(r"(?:^|;)visibility:hidden(?:!important)?(?:;|$)", style):
            return True
        if re.search(r"(?:^|;)opacity:0(?:\.0+)?(?:!important)?(?:;|$)", style):
            return True
    return False


def visible_blocks(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg", "head"]):
        tag.decompose()
    blocks = []
    for tag in soup.find_all(["p", "li", "h1", "h2", "h3", "tr", "dt", "dd"]):
        if is_hidden(tag):
            continue
        value = tag.get_text(" ", strip=True)
        if value and len(value) <= 400:
            blocks.append((value, f"{tag.name}[{len(blocks)+1}]"))
    return blocks


def _coerce(raw: str, kind: str):
    raw = raw.strip().strip(". ")
    if kind == "string":
        return raw[:200] if raw else None
    if kind == "boolean":
        if raw.lower() in {"true", "yes"}:
            return True
        if raw.lower() in {"false", "no"}:
            return False
        return None
    match = re.fullmatch(r"(?:[$€£₹]|USD\s*)?\s*(-?\d+(?:\.\d+)?)\s*(?:(?:/|per\s+)?(?:month|year|day|user|seat|GB)|monthly|annually|USD|dollars)?", raw, re.I)
    if not match:
        return None
    number = float(match.group(1))
    return int(number) if kind == "integer" and number.is_integer() else number if kind == "number" else None


def candidates_for(page: Page, name: str, spec: dict) -> list[Candidate]:
    label = spec.get("title") or name.replace("_", " ")
    if not isinstance(label, str) or len(label) > 80:
        return []
    pattern = re.compile(r"^\s*" + re.escape(label) + r"\s*[:\-–]\s*(.+?)\s*$", re.I)
    results = []
    digest = snapshot_hash(page.html)
    for excerpt, locator in visible_blocks(page.html):
        match = pattern.match(excerpt)
        if not match:
            continue
        value = _coerce(match.group(1), spec["type"])
        if value is not None:
            results.append(Candidate(value=value, evidence=Evidence(source_url=page.url, fetched_at=page.fetched_at,
                excerpt=excerpt, locator=locator, snapshot_hash=digest)))
    # JSON-LD is accepted only when the exact key and scalar occur in retained script content.
    soup = BeautifulSoup(page.html, "html.parser")
    for index, script in enumerate(soup.find_all("script", type="application/ld+json"), 1):
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if isinstance(node, dict) and name in node and isinstance(node[name], (str, int, float, bool)):
                value = _coerce(str(node[name]), spec["type"])
                if value is not None:
                    excerpt = json.dumps({name: node[name]}, ensure_ascii=False)
                    results.append(Candidate(value=value, evidence=Evidence(source_url=page.url, fetched_at=page.fetched_at,
                        excerpt=excerpt, locator=f"script[ld+json][{index}]", snapshot_hash=digest)))
    return results


def verify_candidate(candidate: Candidate, html: str) -> bool:
    if snapshot_hash(html) != candidate.evidence.snapshot_hash:
        return False
    excerpt = candidate.evidence.excerpt
    if candidate.evidence.locator.startswith("script[ld+json]"):
        try:
            expected = json.loads(excerpt)
            soup = BeautifulSoup(html, "html.parser")
            for script in soup.find_all("script", type="application/ld+json"):
                data = json.loads(script.string or "")
                for node in data if isinstance(data, list) else [data]:
                    if isinstance(node, dict) and all(node.get(key) == value for key, value in expected.items()):
                        return True
        except (ValueError, TypeError):
            pass
        return False
    return any(text == excerpt for text, _ in visible_blocks(html))


def extract_fields(pages: list[Page], properties: dict, blocked: bool) -> dict[str, FieldResult]:
    output = {}
    for name, spec in properties.items():
        raw = [candidate for page in pages for candidate in candidates_for(page, name, spec)]
        good = [candidate for candidate in raw if next((verify_candidate(candidate, page.html) for page in pages
                if page.url == candidate.evidence.source_url and snapshot_hash(page.html) == candidate.evidence.snapshot_hash), False)]
        unique = {}
        for candidate in good:
            unique.setdefault(json.dumps(candidate.value, sort_keys=True), []).append(candidate)
        if len(unique) == 1:
            candidates = next(iter(unique.values()))
            output[name] = FieldResult(state="verified", value=candidates[0].value,
                                       evidence=[c.evidence for c in candidates])
        elif len(unique) > 1:
            output[name] = FieldResult(state="conflicting", candidates=[values[0] for values in unique.values()],
                                       reason="Distinct source-backed values")
        elif raw:
            output[name] = FieldResult(state="unverified", reason="Candidate failed source verification")
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

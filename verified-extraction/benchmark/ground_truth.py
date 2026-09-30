"""Independent source checks for customer-task labels, without the extractor's parser."""
from __future__ import annotations

import base64
import hashlib
import re
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup
from bs4.element import Comment, NavigableString, Tag

from verified_extraction.extract import dom_path, is_hidden
from verified_extraction.fetch import decode_html


def normalized(value: str) -> str:
    return " ".join(str(value).split())


def visible_text(node: Tag) -> str:
    """Read only rendered text; a visible parent cannot launder hidden descendants."""
    return normalized(" ".join(str(child) for child in node.descendants
                           if isinstance(child, NavigableString) and not isinstance(child, Comment) and
                           isinstance(child.parent, Tag) and not is_hidden(child.parent) and
                           not child.find_parent(["script", "style", "template"])))


def _node(soup: BeautifulSoup, locator: str) -> Tag:
    found = next((tag for tag in soup.find_all(True) if dom_path(tag) == locator), None)
    if found is None or is_hidden(found):
        raise ValueError(f"Visible source node not found at {locator}")
    return found


def capture_index(manifest: dict) -> dict:
    output = {}
    for page in manifest["pages"].values():
        if "error" in page:
            continue
        raw = base64.b64decode(page["raw_base64"], validate=True)
        digest = hashlib.sha256(raw).hexdigest()
        if digest != page["snapshot_hash"]:
            raise ValueError("Frozen capture hash mismatch")
        decoded, _, _ = decode_html(raw, page["content_type"])
        key = (page["final_url"], digest, page["fetched_at"])
        output[key] = BeautifulSoup(decoded, "html.parser")
    return output


def _ancestor(first: Tag, second: Tag) -> Tag | None:
    ancestors = {id(node): node for node in [first, *first.parents] if isinstance(node, Tag)}
    return next((ancestors[id(node)] for node in [second, *second.parents] if id(node) in ancestors), None)


def _table_column(node: Tag, table: Tag) -> int | None:
    cell = next((tag for tag in [node, *node.parents] if isinstance(tag, Tag) and tag.name in {"td", "th"}), None)
    if cell is None or table not in cell.parents:
        return None
    row = cell.find_parent("tr")
    if row is None:
        return None
    cells = row.find_all(["td", "th"], recursive=False)
    if any(item.has_attr("colspan") or item.has_attr("rowspan") for item in cells):
        return None
    return cells.index(cell)


def _value_supported(raw: str, value, kind: str) -> bool:
    if value is None or not isinstance(raw, str) or not raw.strip():
        return False
    if kind in {"number", "integer"}:
        matches = re.findall(r"(?<![\d.])-?\d+(?:,\d{3})*(?:\.\d+)?(?![\d.])", raw)
        if len(matches) != 1:
            return False
        try:
            parsed = Decimal(matches[0].replace(",", ""))
            wanted = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return False
        return parsed == wanted and (kind != "integer" or parsed == parsed.to_integral_value())
    if kind == "boolean":
        return (str(value).lower() in raw.lower()) if isinstance(value, bool) else False
    left, right = normalized(str(value)).casefold(), normalized(raw).casefold()
    aliases = {"month": ("monthly", "/month", "per month"),
               "year": ("yearly", "annual", "annually", "/year", "per year")}
    return bool(left) and (left in right or any(token in right for token in aliases.get(left, ())))


def validate_claim(manifest: dict, field_name: str, item: dict, captures: dict) -> dict:
    """Validate a human-cited plan/value relationship in one frozen capture."""
    plan = manifest.get("plan_name")
    if not plan or item.get("plan_name") != plan:
        raise ValueError(f"Field {field_name} does not name the preselected plan")
    properties = manifest["schema"]["properties"]
    if field_name not in properties:
        raise ValueError(f"Unknown field {field_name}")
    evidence = item.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError(f"Field {field_name} needs one or more cited claims")
    if item.get("judgment") not in {"explicit", "interpreted"}:
        raise ValueError(f"Field {field_name} needs an explicit or interpreted judgment")
    if item["judgment"] == "interpreted" and not item.get("rationale"):
        raise ValueError(f"Field {field_name} interpretation needs a rationale")
    validated = []
    for source in evidence:
        key = (source.get("source_url"), source.get("snapshot_hash"), source.get("fetched_at"))
        soup = captures.get(key)
        if soup is None:
            raise ValueError(f"Field {field_name} source URL, hash or capture time is unbound")
        scope = _node(soup, source.get("scope_locator", ""))
        nodes = source.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise ValueError(f"Field {field_name} has no source nodes")
        roles = {}
        for cited in nodes:
            role = cited.get("role")
            if role not in {"plan", "value", "qualifier"}:
                raise ValueError(f"Field {field_name} has an invalid source-node role")
            node = _node(soup, cited.get("locator", ""))
            if node is not scope and all(parent is not scope for parent in node.parents):
                raise ValueError(f"Field {field_name} node lies outside its cited scope")
            excerpt = visible_text(node)
            if excerpt != normalized(cited.get("excerpt", "")):
                raise ValueError(f"Field {field_name} source-node excerpt changed")
            roles.setdefault(role, []).append(node)
        if not roles.get("plan") or not roles.get("value"):
            raise ValueError(f"Field {field_name} needs plan and value nodes")
        plan_node, value_node = roles["plan"][0], roles["value"][0]
        if not re.search(r"(?<!\w)" + re.escape(plan) + r"(?!\w)",
                         visible_text(plan_node), re.I):
            raise ValueError(f"Field {field_name} cites a different plan")
        raw_value = source.get("raw_value")
        value_text = visible_text(value_node)
        if not isinstance(raw_value, str) or not re.search(r"(?<!\d)" + re.escape(raw_value) + r"(?!\d)", value_text):
            raise ValueError(f"Field {field_name} raw value is absent from its value node")
        relation = source.get("relation", "same_scope")
        if relation == "same_scope":
            common = _ancestor(plan_node, value_node)
            if common is not scope or scope.name in {"html", "body"}:
                raise ValueError(f"Field {field_name} plan and value do not share the cited local scope")
            headings = scope.find_all(re.compile(r"^h[1-6]$"))
            if len(headings) > 1 and plan_node not in value_node.parents:
                raise ValueError(f"Field {field_name} scope contains multiple plan headings")
        elif relation == "table_column":
            if scope.name != "table" or _table_column(plan_node, scope) is None or \
               _table_column(plan_node, scope) != _table_column(value_node, scope):
                raise ValueError(f"Field {field_name} plan and value are not in one table column")
        elif relation == "shared_all_plans":
            qualifier = " ".join(visible_text(node).casefold()
                                 for node in roles.get("qualifier", []))
            if not re.search(r"\b(all|every) plans\b", qualifier):
                raise ValueError(f"Field {field_name} lacks an all-plans statement")
        else:
            raise ValueError(f"Field {field_name} has an unsupported plan relation")
        if not _value_supported(raw_value, item.get("value"), properties[field_name]["type"]):
            raise ValueError(f"Field {field_name} value is unsupported by its source node")
        context = " ".join(visible_text(node) for group in roles.values() for node in group)
        unit = item.get("unit")
        period = item.get("billing_period")
        if unit and unit.casefold() not in context.casefold() and not \
           (unit == "month" and "monthly" in context.casefold()) and not \
           (unit == "year" and "annual" in context.casefold()):
            raise ValueError(f"Field {field_name} unit is absent from its cited context")
        if period and period.casefold() not in context.casefold() and not \
           (period == "month" and "monthly" in context.casefold()) and not \
           (period == "year" and "annual" in context.casefold()):
            raise ValueError(f"Field {field_name} billing period is absent from its cited context")
        currency = item.get("currency")
        symbols = {"EUR": "€", "GBP": "£", "INR": "₹"}
        if currency and currency.casefold() not in context.casefold() and \
           (currency not in symbols or symbols[currency] not in context):
            raise ValueError(f"Field {field_name} currency is not explicit in its cited context")
        if item.get("ambiguity") and item.get("state") == "verified":
            raise ValueError(f"Field {field_name} ambiguous claim cannot be verified")
        validated.append({"source_url": key[0], "snapshot_hash": key[1], "fetched_at": key[2],
                          "scope_locator": source["scope_locator"], "raw_value": raw_value,
                          "relation": relation,
                          "value_locators": [dom_path(node) for node in roles["value"]]})
    return {"state": item["state"], "value": item["value"], "unit": item.get("unit"),
            "currency": item.get("currency"), "billing_period": item.get("billing_period"),
            "plan_name": plan, "judgment": item["judgment"], "conditions": item.get("conditions"),
            "evidence": validated}


def machine_evidence_in_scope(evidence: list[dict], expected: dict) -> bool:
    sources = expected.get("evidence", [])
    if not sources:
        return True
    return any(item["source_url"] == source["source_url"] and
               item["snapshot_hash"] == source["snapshot_hash"] and
               item["fetched_at"] == source["fetched_at"] and
               (item["locator"] == source["scope_locator"] or
                item["locator"].startswith(source["scope_locator"] + " > ")) and
               (source.get("relation") != "table_column" or any(
                   item["locator"] == locator or item["locator"].startswith(locator + " > ")
                   for locator in source.get("value_locators", [])))
               for item in evidence for source in sources)

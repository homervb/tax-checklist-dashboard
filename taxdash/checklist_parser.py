"""
Parse pasted ProSeries Client Checklist text.

Extracts:
  - Client name near the top
  - Checklist items under "Tax Documents:" and "Additional paperwork:"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


CLIENT_NAME_PATTERNS = [
    re.compile(r"(?i)^\s*client(?:\s+name)?\s*[:\-]\s*(.+)$"),
    re.compile(r"(?i)^\s*taxpayer(?:\s+name)?\s*[:\-]\s*(.+)$"),
    re.compile(r"(?i)^\s*name\s*[:\-]\s*(.+)$"),
    re.compile(r"(?i)^\s*prepared\s+for\s*[:\-]?\s*(.+)$"),
    re.compile(r"(?i)^\s*for\s*[:\-]\s*([A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+)+)\s*$"),
]

TAX_DOCS_HEADER = re.compile(r"(?i)^\s*tax\s+documents?\s*:?\s*$")
ADDITIONAL_HEADER = re.compile(
    r"(?i)^\s*additional\s+(?:paperwork|documents?|items?|information)\s*:?\s*$"
)

SECTION_HEADERS = re.compile(
    r"(?i)^\s*(?:tax\s+documents?|additional\s+(?:paperwork|documents?|items?)|"
    r"documents?\s+needed|please\s+provide|items?\s+(?:needed|requested)|"
    r"checklist|requested\s+documents?|information\s+needed|"
    r"what\s+we\s+need|missing\s+documents?)\s*:?\s*$"
)

CLOSING_HEADER = re.compile(
    r"(?i)^\s*(?:thank\s+you|sincerely|regards|best\s+regards|"
    r"if\s+you\s+have\s+questions|please\s+call|notes?|comments?)\b.*"
)

CATEGORY_HEADER = re.compile(
    r"(?i)^\s*(?:income|deductions?|credits?|expenses?|payments?|"
    r"employment|investments?|retirement|education|health(?:care)?|"
    r"business|rental|dependents?|identity|banking|other)\s*:?\s*$"
)

CHECKBOX_PREFIX = re.compile(
    r"^\s*(?:[\u2610\u2611\u2612\u25A1\u25FB\u25FC\u25A0\u25AF\u274F\u2B1C"
    r"\u0394\u25AD]|\[\s*[xX✓✔]?\s*\]|\(\s*[xX]?\s*\))\s*(.+)$"
)

BULLET_OR_NUMBER = re.compile(
    r"^\s*(?:[\-\*\u2022\u2023\u25E6\u2043\u2219\u00B7\u25AA\u25CF\u25CB]|"
    r"[0-9]{1,3}[.)]|[A-Za-z][.)])\s+(.+)$"
)

LEADING_BULLET = re.compile(
    r"^[\-\*\u2022\u2023\u25E6\u2043\u2219\u00B7\u25AA\u25CF\u25CB"
    r"\u2610\u2611\u25A1\[\]]+\s*"
)

INSTRUCTIONAL_LINE = re.compile(
    r"(?i)^\s*(?:please\s+(?:provide|gather|bring|return)|thank\s+you|sincerely|"
    r"if\s+you\s+have\s+questions|call\s+our\s+office|"
    r"bring\s+originals|long\s+explanatory).*"
)

DOC_KEYWORDS = re.compile(
    r"(?i)\b("
    r"w-?2|w-?2[cg]|1099(?:-[A-Z]{1,4})?|1098(?:-[A-Z]{1,3})?|1095-?[ABC]?|"
    r"k-?1|schedule\s+[A-F]|1040|941|940|"
    r"brokerage|closing\s+statement|settlement\s+statement|"
    r"property\s+tax|mortgage\s+interest|charitable|donation|"
    r"medical\s+expense|health\s+insurance|hsa|fsa|"
    r"dependent|driver.?s?\s+license|id\s+card|"
    r"bank\s+statement|voided\s+check|direct\s+deposit|"
    r"prior[\-\s]?year|organizer|questionnaire|"
    r"crypto|stock\s+sale|cost\s+basis|"
    r"rental|business\s+expense|mileage|home\s+office|"
    r"estimated\s+tax|extension|irs\s+notice|state\s+return|"
    r"wage|dividend|interest\s+income"
    r")\b"
)

SKIP_LINE = re.compile(
    r"(?i)^\s*(?:page\s+\d+(?:\s+of\s+\d+)?|confidential|proseries|intuit|copyright|"
    r"www\.|https?://|phone|fax|email\s*:|date\s*:|tax\s+year|"
    r"please\s+return|thank\s+you|sincerely).*$"
)

ADDRESS_LINE = re.compile(
    r"(?i)^\s*(?:\d{1,6}\s+\S+|p\.?\s*o\.?\s*box\b|"
    r"[A-Za-z .'-]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?|"
    r"suite\s+\d+|apt\.?\s*\d+)\s*$"
)

NAME_LIKE = re.compile(
    r"^[A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-\.]+)+$"
)

FIRM_HINT = re.compile(
    r"(?i)\b("
    r"cpa|llc|llp|p\.?c\.?|"
    r"tax\s*&\s*accounting|tax\s+and\s+accounting|accounting\s+(?:firm|group)|"
    r"tax\s+(?:group|firm|associates|advisors?|services?)|"
    r"bookkeeping|associates|partners\b|"
    r"advisory\s+group"
    r")\b"
)


@dataclass
class ParseResult:
    client_name: str = ""
    items: list[str] = field(default_factory=list)
    raw_text: str = ""
    warnings: list[str] = field(default_factory=list)


def parse_checklist_text(raw_text: str) -> ParseResult:
    """Parse pasted checklist text into client name + items."""
    body = (raw_text or "").strip()
    # Ignore legacy diagnostic wrappers if someone pastes a debug dump
    if "=== extracted text below ===" in body:
        body = body.split("=== extracted text below ===", 1)[1].strip()
        if body == "(no text recovered from any engine)":
            body = ""

    result = ParseResult(raw_text=raw_text or "")
    if not body:
        result.warnings.append("No checklist text provided.")
        return result

    lines = [ln.rstrip() for ln in body.splitlines()]
    non_empty = [ln.strip() for ln in lines if ln.strip()]

    result.client_name = _guess_client_name(non_empty)
    if not result.client_name:
        result.warnings.append(
            "Could not confidently detect client name — please set it manually."
        )

    result.items = _guess_items_proseries(non_empty)
    if not result.items:
        result.items = _guess_items_generic(non_empty)
    if not result.items:
        result.warnings.append(
            "No checklist items detected — review the pasted text and add items manually."
        )

    result.raw_text = body
    return result


def items_from_multiline(text: str) -> list[str]:
    """Split an editable textarea into checklist item strings."""
    items: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = _clean_item(raw)
        if not line or INSTRUCTIONAL_LINE.match(line):
            continue
        if line.startswith("===") or line.startswith("file:") or line.startswith("engines:"):
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(line)
    return items


def _split_dual_column_line(line: str) -> list[str]:
    parts = re.split(r"\s{2,}", line.strip())
    if len(parts) >= 2:
        return [p.strip() for p in parts if p.strip()]
    if FIRM_HINT.search(line):
        words = line.split()
        for i in range(1, len(words)):
            right = " ".join(words[i:])
            left = " ".join(words[:i])
            if FIRM_HINT.search(right) and NAME_LIKE.match(left):
                return [left, right]
    return [line.strip()] if line.strip() else []


def _guess_client_name(lines: list[str]) -> str:
    expanded: list[str] = []
    for line in lines:
        expanded.extend(_split_dual_column_line(line))

    head = expanded[:40]
    for line in head:
        for pattern in CLIENT_NAME_PATTERNS:
            m = pattern.match(line)
            if m:
                name = _clean_name(m.group(1))
                if name:
                    return name

    for line in head[:16]:
        if SKIP_LINE.match(line) or SECTION_HEADERS.match(line):
            continue
        if ADDRESS_LINE.match(line):
            continue
        if DOC_KEYWORDS.search(line):
            continue
        if len(line) > 60:
            continue
        for prefix in (
            r"(?i)^client\s+checklist\s+for\s+",
            r"(?i)^checklist\s+for\s+",
            r"(?i)^organizer\s+for\s+",
        ):
            stripped = re.sub(prefix, "", line).strip()
            if stripped != line:
                line = stripped
                break
        if FIRM_HINT.search(line):
            continue
        if NAME_LIKE.match(line) and not line.lower().startswith(
            ("please", "documents", "tax", "additional")
        ):
            return _clean_name(line)

    return ""


def _clean_name(value: str) -> str:
    value = value.strip(" \t-:;,.|")
    value = re.sub(r"\s{2,}", " ", value)
    value = re.sub(r"\s+\d{4}\s*(?:tax\s*)?year.*$", "", value, flags=re.I)
    if len(value) < 3 or len(value) > 80:
        return ""
    return value


def _guess_items_proseries(lines: list[str]) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    mode: Optional[str] = None

    for line in lines:
        if TAX_DOCS_HEADER.match(line):
            mode = "tax"
            continue
        if ADDITIONAL_HEADER.match(line):
            mode = "additional"
            continue
        if mode and CLOSING_HEADER.match(line):
            mode = None
            continue
        if mode and SECTION_HEADERS.match(line) and not (
            TAX_DOCS_HEADER.match(line) or ADDITIONAL_HEADER.match(line)
        ):
            mode = None
            continue

        if mode is None:
            continue

        if CATEGORY_HEADER.match(line):
            continue
        if SKIP_LINE.match(line) or INSTRUCTIONAL_LINE.match(line):
            continue
        if ADDRESS_LINE.match(line):
            continue
        if FIRM_HINT.search(line) or re.search(r"\s{2,}", line):
            continue
        if NAME_LIKE.match(line) and not DOC_KEYWORDS.search(line):
            continue

        candidate = _line_to_item(line)
        if not candidate:
            continue
        if len(candidate) > 140 or candidate.count(".") >= 2:
            continue
        if len(candidate.split()) > 22 and not DOC_KEYWORDS.search(candidate):
            continue
        if ADDRESS_LINE.match(candidate) or FIRM_HINT.search(candidate):
            continue

        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(candidate)

    return items


def _guess_items_generic(lines: list[str]) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    in_section = False

    for line in lines:
        if SECTION_HEADERS.match(line):
            in_section = True
            continue
        if in_section and CLOSING_HEADER.match(line):
            in_section = False
            continue

        extracted = _line_to_item(line)
        candidate = None
        if extracted and (
            in_section
            or CHECKBOX_PREFIX.match(line)
            or BULLET_OR_NUMBER.match(line)
            or DOC_KEYWORDS.search(line)
        ):
            candidate = extracted

        if not candidate:
            continue
        if SKIP_LINE.match(candidate) or INSTRUCTIONAL_LINE.match(candidate):
            continue
        if SECTION_HEADERS.match(candidate) or CATEGORY_HEADER.match(candidate):
            continue
        if len(candidate) < 3 or len(candidate) > 140:
            continue
        if candidate.count(".") >= 2:
            continue

        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(candidate)

    return items


def _line_to_item(line: str) -> Optional[str]:
    m = CHECKBOX_PREFIX.match(line)
    if m:
        return _clean_item(m.group(1))
    m = BULLET_OR_NUMBER.match(line)
    if m:
        return _clean_item(m.group(1))
    cleaned = _clean_item(line)
    if cleaned and 3 <= len(cleaned) <= 140:
        return cleaned
    return None


def _clean_item(value: str) -> str:
    value = value.strip()
    m = CHECKBOX_PREFIX.match(value)
    if m:
        value = m.group(1).strip()
    m = BULLET_OR_NUMBER.match(value)
    if m:
        value = m.group(1).strip()
    value = LEADING_BULLET.sub("", value).strip()
    value = value.strip(" \t-:;|")
    value = re.sub(r"^[\u2610\u2611\u25A1\[\]]+\s*", "", value).strip()
    value = re.sub(r"^(?:CL|_\]|\[\]|\|_)\s+", "", value).strip()
    return value

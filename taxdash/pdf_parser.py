"""Compatibility shim — text parsing lives in taxdash.checklist_parser."""

from taxdash.checklist_parser import (  # noqa: F401
    ParseResult,
    items_from_multiline,
    parse_checklist_text,
)

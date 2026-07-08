"""Detect a property type from a workbook filename or sheet name."""

from __future__ import annotations

import re

PROPERTY_TYPE_KEYWORDS: dict[str, list[str]] = {
    "Apartment": ["apartment", "apt", "flat"],
    "Bungalow": ["bungalow", "villa", "independent house", "row house"],
    "Plot": ["plot", "land"],
    "Penthouse": ["penthouse"],
    "Mixed / Other": ["excel", "mixed", "other", "ivr"],
}

# "Mixed / Other" is now also auto-detected for certain catch-all campaign names.
PROPERTY_TYPE_OPTIONS = ["Apartment", "Bungalow", "Plot", "Penthouse", "Mixed / Other"]


def detect_property_type(name: str) -> str | None:
    """Match filename/sheet-name keywords to a property type; None -> manual."""
    # Collapse separators (_ - .) to spaces so word boundaries work on any naming style.
    text = re.sub(r"[^a-z0-9]+", " ", name.lower())
    for prop_type, keywords in PROPERTY_TYPE_KEYWORDS.items():
        for kw in keywords:
            # Allow optional trailing 's' for simple plurals (e.g. apartment -> apartments)
            if re.search(rf"\b{re.escape(kw)}s?\b", text):
                return prop_type
    return None


def detect_sheet_property_type(filename: str, sheet_name: str) -> str | None:
    """Sheet-level detection: the sheet's own name wins, then the filename."""
    return detect_property_type(sheet_name) or detect_property_type(filename)

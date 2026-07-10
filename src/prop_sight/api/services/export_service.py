"""Download the classified leads: Excel per class, plus a Meta audience CSV."""

from __future__ import annotations

import io
import re

import pandas as pd

from ...analytics import rules
from ...analytics.budget import parse_budget
from ...analytics.report import classify_frame
from ...ingestion.mojibake import fold_styled_letters
from ...ingestion.phone import normalize_phone

# Derived bookkeeping the user never asked to export.
_INTERNAL = {
    rules.REASON_COL, "budget_value", "phone_normalized",
    "merged_row_count", "merged_sources",
}


def exportable_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in map(str, df.columns) if c not in _INTERNAL]


def classified_slice(df: pd.DataFrame, rule_list: list[dict], category: str) -> pd.DataFrame:
    """Rows of one class, with the classification reason kept as the last column.

    The reason travels with the export on purpose: a colleague opening
    `unclassified_leads.xlsx` needs to see *why* each lead fell through, not just
    that it did.
    """
    classified = classify_frame(df, rule_list)
    if category not in (*rules.CATEGORIES, rules.UNCLASSIFIED):
        raise ValueError(f"Unknown lead class {category!r}")
    return classified[classified[rules.CLASS_COL] == category]


def to_excel(df: pd.DataFrame, columns: list[str] | None = None) -> bytes:
    """Serialize to .xlsx in memory. Requested columns that don't exist are skipped."""
    wanted = [c for c in (columns or exportable_columns(df)) if c in df.columns]
    if not wanted:
        wanted = exportable_columns(df)

    frame = df[wanted].copy()
    # Keep the human-readable reason on the end when it was requested.
    if rules.REASON_COL in df.columns and rules.REASON_COL not in frame.columns:
        frame[rules.REASON_COL] = df[rules.REASON_COL]

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Leads")
    return buffer.getvalue()


# ── Meta / Facebook customer-list audience ───────────────────────────────────

# Meta's customer list schema. `value` needs `currency` beside it or the column
# is ignored on upload.
META_COLUMNS = ["email", "phone", "fn", "ln", "ct", "st", "country", "value", "currency"]

_NON_LETTER = re.compile(r"[^a-z]")


def _split_name(value: object) -> tuple[str, str]:
    """First / last name. Returns ('','') for a name we cannot use.

    Names arriving from Privyr's ad forms are sometimes in styled Unicode
    ('𝑹𝒂𝒏𝒋𝒊𝒕'), which Meta cannot match against anything; they are folded to
    ASCII here. A cell holding no letters at all is not a name and is dropped
    rather than uploaded as noise.
    """
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return "", ""
    text = re.sub(r"\s+", " ", fold_styled_letters(str(value))).strip()
    if not text or not _NON_LETTER.sub("", text.lower()):
        return "", ""
    parts = text.split(" ")
    return parts[0], " ".join(parts[1:])


def _meta_phone(value: object) -> str:
    """E.164 for India, or blank. Reuses the ingest normalizer, so `+91 0…` and
    the `+1`-typo numbers come out right instead of being uploaded verbatim."""
    mobile = normalize_phone(value)
    return f"+91{mobile}" if mobile else ""


def _meta_value(budget: object) -> str:
    """Budget in rupees. Blank when unparseable — a wrong value is worse than none."""
    if budget is None or (not isinstance(budget, str) and pd.isna(budget)):
        return ""
    lakh = parse_budget(budget)
    return "" if lakh is None else str(int(round(lakh * 100_000)))


def _first_present(row: pd.Series, *fields: str) -> object:
    for field in fields:
        if field in row.index and pd.notna(row[field]) and str(row[field]).strip():
            return row[field]
    return None


def to_meta_audience_csv(df: pd.DataFrame) -> bytes:
    """Build a Meta customer-list CSV from canonical fields.

    Canonical fields, not header guessing: the standalone sorter searched for the
    first column whose name contained "name", which on a Privyr export is
    `Company Name`. Every row's `fn` was a company.

    Rows with neither an email nor a phone are dropped — Meta cannot match them,
    and they only depress the reported match rate.
    """
    if df.empty:
        return b"" + ",".join(META_COLUMNS).encode("utf-8")

    records = []
    for _, row in df.iterrows():
        first, last = _split_name(_first_present(row, "name"))
        phone = _meta_phone(_first_present(row, "phone", "phone_2"))
        email = str(_first_present(row, "email") or "").strip().lower()
        if not phone and not email:
            continue

        value = _meta_value(_first_present(row, "budget"))
        records.append(
            {
                "email": email,
                "phone": phone,
                "fn": first,
                "ln": last,
                "ct": str(_first_present(row, "current_city", "current_location") or "").strip(),
                "st": "",
                "country": "IN",
                "value": value,
                "currency": "INR" if value else "",
            }
        )

    frame = pd.DataFrame(records, columns=META_COLUMNS)
    return frame.to_csv(index=False).encode("utf-8-sig")

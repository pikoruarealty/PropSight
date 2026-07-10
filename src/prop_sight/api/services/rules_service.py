"""Rule storage and the field catalogue the rule editor is built from."""

from __future__ import annotations

import pandas as pd

from ...analytics.common import clean_str
from ...analytics.report import prepare_frame
from ...analytics.rules import CLASS_COL, REASON_COL, RuleError, validate_rules
from ...ingestion.notes import NOTE_PREFIX
from .. import db

# Bookkeeping columns a user must never write a rule against.
_HIDDEN_FIELDS = {
    CLASS_COL, REASON_COL, "budget_value", "phone_normalized",
    "merged_row_count", "merged_sources", "source_sheet", "project",
}
# Free-text columns with one distinct value per lead — a dropdown of 3,700
# unique names is not a filter, it is a phone book.
_UNGROUPABLE = {"name", "email", "phone", "qualitative_remarks", "phone_2"}

# Beyond this many distinct values a field stops being useful to group by.
MAX_DISTINCT_VALUES = 300


def load_rules() -> list[dict]:
    try:
        return validate_rules(db.read_rules())
    except RuleError as exc:
        # A rule set that no longer validates (a field renamed, an operator
        # dropped) must not take the dashboard down with it — every lead simply
        # goes back to unclassified until someone fixes the rules.
        print(f"Ignoring invalid stored rules: {exc}")
        return []


def save_rules(rules: object) -> list[dict]:
    """Validate then persist. Raises RuleError on anything malformed."""
    cleaned = validate_rules(rules)
    db.write_rules(cleaned)
    return cleaned


def _field_label(field: str) -> str:
    if field.startswith(NOTE_PREFIX):
        return f"{field[len(NOTE_PREFIX):]}  (from notes)"
    return field.replace("_", " ")


def field_catalogue(df: pd.DataFrame) -> list[dict]:
    """Every field a rule can target, with its distinct values and fill rate.

    Fields with no data at all are omitted — a dropdown offering a column that
    is empty for every lead can only produce a rule that never fires.
    `note:` columns are included, which is what lets a Privyr upload be sorted on
    an ad-form answer that exists nowhere else.
    """
    if df is None or df.empty:
        return []

    # The same preparation the charts and the rule engine see, so the values
    # offered here are exactly the values a rule will match.
    frame = prepare_frame(df)
    total = len(frame)
    out: list[dict] = []

    for field in frame.columns:
        field = str(field)
        if field in _HIDDEN_FIELDS or field in _UNGROUPABLE:
            continue
        # pandas names headerless spreadsheet columns 'Unnamed: 4'. Whatever is
        # in them, the user cannot know what a rule against them would mean.
        if field.startswith("Unnamed:"):
            continue

        values = clean_str(frame[field])
        filled = int(values.notna().sum())
        if not filled:
            continue

        distinct = values.dropna().unique()
        if len(distinct) > MAX_DISTINCT_VALUES:
            continue

        out.append(
            {
                "field": field,
                "label": _field_label(field),
                "from_notes": field.startswith(NOTE_PREFIX),
                "filled": filled,
                "fill_rate": round(filled / total, 4),
                "values": sorted(str(v) for v in distinct),
            }
        )

    # Best-covered fields first: those are the ones a rule can actually act on.
    out.sort(key=lambda f: (-f["fill_rate"], f["field"]))
    return out

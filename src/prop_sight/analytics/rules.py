"""User-defined lead classification: Good / Bad, and why.

A rule is a list of conditions ANDed together plus the class to assign:

    {"conditions": [{"field": "buying_status", "op": "is",
                     "values": ["Still searching", "Interested"]},
                    {"field": "budget_bucket", "op": "is",
                     "values": ["5-7 Cr", "7-10 Cr", "10+ Cr"]}],
     "category": "good"}

Rules are evaluated top to bottom and the first match wins, so ordering encodes
precedence exactly as a human reads it. A lead matched by no rule is
*unclassified* — that is a statement about the rules, not about the lead, and it
comes with a reason naming the fields that made every rule inapplicable.

Rules bind to **canonical fields**, never to raw spreadsheet headers. A rule
written against `configuration_required` fires on the legacy sheets (which spell
it "Configuration Required"), on the CRM CSV ("Configuration Needed"), and on
Privyr (where it is a question inside the notes column, lifted out by
`ingestion.notes`). One rule set, every format — including formats that do not
exist yet.

Values are compared after the same folding the dashboard applies, so a rule
listing "Bungalow" matches a row that arrived as "Bungalow only".
"""

from __future__ import annotations

import pandas as pd

from .common import clean_str

GOOD = "good"
BAD = "bad"
UNCLASSIFIED = "unclassified"

CATEGORIES = (GOOD, BAD)
CLASS_LABELS = {GOOD: "Good", BAD: "Bad", UNCLASSIFIED: "Unclassified"}

# Column names attached by `classify`.
CLASS_COL = "lead_class"
REASON_COL = "lead_class_reason"

OPERATORS = {
    "is": "is one of",
    "is_not": "is not one of",
    "is_empty": "is empty",
    "is_not_empty": "has any value",
    "contains": "contains",
}
# Operators that ignore the `values` list entirely.
_VALUELESS = {"is_empty", "is_not_empty"}


class RuleError(ValueError):
    """A rule that cannot be evaluated as written."""


def validate_rules(rules: object, known_fields: set[str] | None = None) -> list[dict]:
    """Coerce arbitrary JSON into well-formed rules, or raise.

    Empty conditions are dropped and rules left with none are discarded, mirroring
    what the editor does on save. A rule whose every condition was junk would
    otherwise match every row and silently reclassify the whole database.
    """
    if not isinstance(rules, list):
        raise RuleError("Rules must be a list.")

    cleaned: list[dict] = []
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            raise RuleError(f"Rule #{index} is not an object.")

        category = str(rule.get("category", GOOD)).lower()
        if category not in CATEGORIES:
            raise RuleError(f"Rule #{index}: category must be one of {list(CATEGORIES)}.")

        conditions = []
        for cond in rule.get("conditions") or []:
            if not isinstance(cond, dict):
                continue
            field = str(cond.get("field") or "").strip()
            op = str(cond.get("op") or "is").strip()
            values = cond.get("values") or []
            if not field or op not in OPERATORS:
                continue
            if known_fields is not None and field not in known_fields:
                raise RuleError(f"Rule #{index}: unknown field {field!r}.")
            if op not in _VALUELESS:
                values = [str(v) for v in values if str(v).strip() != "" or v == ""]
                if not values:
                    continue
            else:
                values = []
            conditions.append({"field": field, "op": op, "values": values})

        if conditions:
            cleaned.append({"conditions": conditions, "category": category})

    return cleaned


def describe_rule(rule: dict) -> str:
    parts = [
        f"{c['field']} {OPERATORS[c['op']]}"
        + (f" {', '.join(c['values'])}" if c["values"] else "")
        for c in rule["conditions"]
    ]
    return " AND ".join(parts)


def _condition_mask(df: pd.DataFrame, cond: dict) -> pd.Series:
    """Boolean mask for one condition. A missing column matches nothing.

    A column absent from this slice is not the same as a column full of blanks
    for `is_empty` — but for classification they mean the same thing to the user:
    the lead has no value there. Absent columns therefore satisfy `is_empty`.
    """
    field, op = cond["field"], cond["op"]

    if field not in df.columns:
        return pd.Series(op == "is_empty", index=df.index)

    values = clean_str(df[field])

    if op == "is_empty":
        return values.isna()
    if op == "is_not_empty":
        return values.notna()

    if op == "contains":
        needles = [v.lower() for v in cond["values"]]
        lowered = values.str.lower()
        mask = pd.Series(False, index=df.index)
        for needle in needles:
            mask |= lowered.str.contains(needle, regex=False, na=False)
        return mask

    # `is` / `is_not`. Blank cells never match `is`, and always match `is_not`:
    # "buying_status is not Bad" should not quietly assert something about a
    # lead whose buying status was never recorded.
    matched = values.isin(cond["values"]).fillna(False)
    return matched if op == "is" else (~matched & values.notna())


def _rule_mask(df: pd.DataFrame, rule: dict) -> pd.Series:
    mask = pd.Series(True, index=df.index)
    for cond in rule["conditions"]:
        mask &= _condition_mask(df, cond)
        if not mask.any():
            break
    return mask


def _unclassified_reasons(df: pd.DataFrame, rules: list[dict], unmatched: pd.Series) -> pd.Series:
    """Explain, per unmatched row, which fields kept every rule from applying.

    "No rule matched" is useless. "budget_bucket and configuration_required are
    empty" tells the user their rules are fine and their *data* is thin — the
    single most common reason a lead falls through, given how sparse these
    exports are.
    """
    reasons = pd.Series("", index=df.index, dtype=object)
    if not rules:
        return reasons.mask(unmatched, "No categorization rules are defined yet")

    # A field a rule compares against, or requires a value in, can block the rule
    # by being empty. `is_empty` cannot: a blank there would have matched.
    fields = sorted(
        {
            c["field"]
            for r in rules
            for c in r["conditions"]
            if c["op"] != "is_empty"
        }
    )
    if not fields:
        return reasons.mask(unmatched, "No rule matched this lead")

    present = [f for f in fields if f in df.columns]
    missing_cols = [f for f in fields if f not in df.columns]

    blank = pd.DataFrame(
        {f: clean_str(df[f]).isna() for f in present},
        index=df.index,
    )
    for f in missing_cols:
        blank[f] = True

    def _reason(row: pd.Series) -> str:
        empty = [f for f in fields if row[f]]
        if not empty:
            return "Values did not match any rule"
        if len(empty) == len(fields):
            return f"No data in any rule field ({', '.join(empty)})"
        return f"No data in {', '.join(empty)}"

    computed = blank.apply(_reason, axis=1)
    return reasons.mask(unmatched, computed)


def classify(df: pd.DataFrame, rules: list[dict]) -> pd.DataFrame:
    """Attach `lead_class` and `lead_class_reason` columns.

    Never mutates the caller's frame. With no rules every lead is unclassified,
    which is correct: the user has not told us what "good" means.
    """
    out = df.copy()
    out[CLASS_COL] = UNCLASSIFIED
    out[REASON_COL] = ""

    remaining = pd.Series(True, index=out.index)
    for index, rule in enumerate(rules, start=1):
        if not remaining.any():
            break
        # First match wins: only rows no earlier rule claimed are still eligible.
        mask = remaining & _rule_mask(out, rule)
        if not mask.any():
            continue
        out.loc[mask, CLASS_COL] = rule["category"]
        out.loc[mask, REASON_COL] = f"Rule #{index}: {describe_rule(rule)}"
        remaining &= ~mask

    out.loc[remaining, REASON_COL] = _unclassified_reasons(out, rules, remaining)[remaining]
    return out


def classification_summary(df: pd.DataFrame) -> dict:
    """Counts per class plus the reasons leads went unclassified, largest first."""
    if CLASS_COL not in df.columns:
        return {"enabled": False}

    counts = df[CLASS_COL].value_counts()
    unclassified = df[df[CLASS_COL] == UNCLASSIFIED]
    reasons = unclassified[REASON_COL].value_counts()
    total = int(len(df))

    return {
        "enabled": True,
        "total": total,
        "good": int(counts.get(GOOD, 0)),
        "bad": int(counts.get(BAD, 0)),
        "unclassified": int(counts.get(UNCLASSIFIED, 0)),
        "classified_share": round((total - int(counts.get(UNCLASSIFIED, 0))) / total, 4) if total else 0,
        "unclassified_reasons": [
            {"reason": str(reason), "count": int(count)} for reason, count in reasons.head(8).items()
        ],
    }

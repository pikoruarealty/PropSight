"""Turn `Label: value` lines buried in a free-text column into real columns.

Why this exists
---------------
Categorization rules group leads by "column = value". That premise collapses on
a Privyr export, where the lead's budget, city, and every ad-form answer live
inside one prose `Notes` cell:

    Campaign: bungalow ahmd general - quality lead
    Adset: Bungalow Ahmd - NEW Quality Lead
    Full Name: Hemal Gohil
    City: Ahmedabad
    What Is Your Budget?: inr_4_cr_to_6_cr
    Are You Ready To Proceed With A Refundable Eoi (Rs 5L)?: yes,_i_can_do_it_online

There is nothing to group by: the whole blob is one distinct value per lead.

This module lifts every label out of the text and gives it a column, so the
sorter and the analytics see the same shape of data regardless of which CRM
produced the file:

  * A label that means a field we already know about ('What Is Your Budget?')
    backfills that canonical column, so budget bands, cross-tabs and rules all
    work on Privyr rows exactly as they do on the legacy sheets.
  * A label we have no field for ('Are You Ready To Proceed…?') becomes a
    `note:` column. It is still a real, groupable, exportable column — the whole
    point is that the user can sort on it — we simply make no claim about what
    it means.

Which labels are real
---------------------
A colon appears in ordinary prose ("called at 5:30", "update: not reachable").
A label therefore has to *recur*: it must appear on at least `MIN_ROWS` rows, or
on `MIN_SHARE` of the rows that have any notes at all. One rep's stray colon
never becomes a column; a form question asked of every lead always does.
"""

from __future__ import annotations

import re

import pandas as pd

from .normalize import CANONICAL_FIELDS

# A labelled line: start-of-line, a short label, a colon, then the value.
# Privyr terminates lines inside a cell with the literal `_x000D_`.
_LINE_BREAK = r"(?:_x000D_|\r|\n)"
_LABEL_LINE = re.compile(
    rf"(?:^|{_LINE_BREAK})[ \t]*([^\r\n:]{{2,60}}?)[ \t]*:[ \t]*([^\r\n]*?)(?={_LINE_BREAK}|$)"
)

# A label has to read like a label, not like a clock time or a sentence.
_LABEL_OK = re.compile(r"^[A-Za-z][A-Za-z0-9 '&/()?.,\-₹ ]*$")
_MAX_LABEL_WORDS = 10

MIN_ROWS = 2
MIN_SHARE = 0.02
NOTE_PREFIX = "note:"

# Columns worth scanning for embedded labels, most likely first.
TEXT_COLUMNS = ("qualitative_remarks", "notes", "remarks")

# Labels whose meaning we already have a canonical field for. Keyed by `_label_key`.
NOTE_LABEL_ALIASES: dict[str, str] = {
    "fullname": "name",
    "name": "name",
    "leadname": "name",
    "email": "email",
    "emailaddress": "email",
    "phonenumber": "phone",
    "phone": "phone",
    "mobile": "phone",
    "mobilenumber": "phone",
    "contactnumber": "phone",
    "whatsappnumber": "phone",
    "city": "current_city",
    "currentcity": "current_city",
    "companyname": "company_name",
    "company": "company_name",
    "jobtitle": "job_title",
    "designation": "job_title",
    "occupation": "business_profile",
    "profession": "business_profile",
    # The ad platform writes these three into the notes preamble.
    "campaign": "campaign",
    "adset": "facebook_ad_set",
    "ad": "facebook_ad",
    # Budget is asked with a different question on every ad form.
    "budget": "budget",
    "yourbudget": "budget",
    "whatisyourbudget": "budget",
    "whatbudgetareyoucomfortablewith": "budget",
    "whatsyourbudget": "budget",
    "budgetrange": "budget",
    # Configuration likewise.
    "configuration": "configuration_required",
    "unittype": "configuration_required",
    "whatconfigurationareyoulookingfor": "configuration_required",
    "whichconfigurationareyouinterestedin": "configuration_required",
    "purpose": "purpose_of_buying",
    "purposeofbuying": "purpose_of_buying",
    "preferredlocation": "location_preference",
    "locationpreference": "location_preference",
    "lookingsince": "looking_since",
}

# Privyr slugifies the answers the lead picked: 'inr_4_cr_to_6_cr', '3_bhk'.
_CURRENCY_PREFIX = re.compile(r"^(?:inr|rs)[_\s]+", re.IGNORECASE)
# Not `\b`: '7cr' has no word boundary between the digit and the unit.
_UNIT_TOKENS = re.compile(r"(?<![a-z])(bhk|cr|rk|inr|eoi|nri)(?![a-z])", re.IGNORECASE)
_SLUG = re.compile(r"_|^(?:inr|rs)[_\s]", re.IGNORECASE)


def _label_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", label.lower())


def _is_label(label: str) -> bool:
    return bool(_LABEL_OK.match(label)) and len(label.split()) <= _MAX_LABEL_WORDS


def deslug(value: str) -> str:
    """Make a slugified form answer readable, and leave everything else alone.

    'inr_4_cr_to_6_cr' -> '4 CR to 6 CR'
    'yes,_i_can_do_it_online' -> 'Yes, i can do it online'

    The rewrite is gated on the value actually *being* a slug (it contains an
    underscore, or a currency prefix). Applied unconditionally it would
    capitalize 'hemal@test.com' and uppercase fragments of ordinary prose —
    every labelled line goes through here, not just the multiple-choice ones.
    """
    text = re.sub(r"\s+", " ", value.strip())
    if not text or not _SLUG.search(text):
        return text

    text = _CURRENCY_PREFIX.sub("", text).replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return text
    text = _UNIT_TOKENS.sub(lambda m: m.group(1).upper(), text)
    return text[0].upper() + text[1:] if text[0].islower() else text


def _pairs(text: object) -> list[tuple[str, str]]:
    if not isinstance(text, str) or ":" not in text:
        return []
    out = []
    for label, value in _LABEL_LINE.findall(text):
        label = label.strip()
        value = value.strip()
        if label and value and _is_label(label):
            out.append((label, value))
    return out


def _text_column(df: pd.DataFrame) -> str | None:
    for col in TEXT_COLUMNS:
        if col in df.columns and df[col].notna().any():
            return col
    return None


def count_labels(series: pd.Series) -> dict[str, int]:
    """Every label seen in the column, mapped to the number of rows carrying it."""
    noted = series.dropna()
    if noted.empty:
        return {}

    counts: dict[str, int] = {}
    seen_label: dict[str, str] = {}
    for text in noted:
        # A label repeated inside one cell still counts once for that row.
        for slug, label in {_label_key(lbl): lbl for lbl, _ in _pairs(text)}.items():
            counts[slug] = counts.get(slug, 0) + 1
            seen_label.setdefault(slug, label)

    return {seen_label[slug]: n for slug, n in counts.items()}


def discover_labels(
    series: pd.Series, extra_label_map: dict[str, str] | None = None
) -> dict[str, int]:
    """Labels worth turning into a column, most common first.

    The recurrence threshold exists only to stop ordinary prose ("called at
    5:30", "update: not reachable") from minting columns. A label we already
    recognize — 'What Budget Are You Comfortable With?' — needs no such
    protection: we know what it means, so one occurrence is worth keeping. Ad
    forms routinely word the same question two ways across campaigns, and the
    rarer wording is exactly the one a frequency filter would throw away.
    """
    counts = count_labels(series)
    if not counts:
        return {}

    extra = {_label_key(k): v for k, v in (extra_label_map or {}).items()}
    noted = int(series.notna().sum())
    threshold = max(MIN_ROWS, int(noted * MIN_SHARE))

    kept = {
        label: n
        for label, n in counts.items()
        if n >= threshold or _label_key(label) in NOTE_LABEL_ALIASES or _label_key(label) in extra
    }
    return dict(sorted(kept.items(), key=lambda kv: -kv[1]))


def _extract_row(text: object, wanted: dict[str, list[str]]) -> dict[str, str]:
    """Pull `wanted` (slug -> destination columns) out of one cell's labelled lines."""
    found: dict[str, str] = {}
    for label, value in _pairs(text):
        for target in wanted.get(_label_key(label), ()):
            found.setdefault(target, value)
    return found


def note_columns(df: pd.DataFrame) -> list[str]:
    """The `note:` columns present on a frame, in their existing order."""
    return [c for c in df.columns if str(c).startswith(NOTE_PREFIX)]


def extract_note_fields(
    df: pd.DataFrame, extra_label_map: dict[str, str] | None = None
) -> tuple[pd.DataFrame, dict]:
    """Lift recurring labelled lines out of the notes column into real columns.

    `extra_label_map` maps a raw label to a canonical field, for labels the
    static alias table does not know (supplied by the LLM).

    Every label the static table does not recognize gets a `note:<Label>` column
    **even when the LLM found a canonical home for it**. The LLM's mapping is a
    guess and is not reproducible — it once decided this client's `Timeline`
    ("how soon will they buy") meant `looking_since` ("how long have they been
    searching"), which is close to the opposite. If groupability depended on that
    guess, the field a user built a rule against could vanish on the next upload.
    So the guess is used to *enrich* the canonical column, and the raw question
    always survives as a column of its own.

    A canonical column is only ever *backfilled*: a value maintained in a real
    spreadsheet column outranks a frozen snapshot of what the lead typed into an
    ad form. `note:` columns are written unconditionally, since nothing else
    owns them.

    Returns (frame, info) where info records what was discovered, so the upload
    UI can show the user which fields were recovered from prose.
    """
    text_col = _text_column(df)
    info: dict = {"text_column": text_col, "labels": {}, "canonical": {}, "note_columns": []}
    if text_col is None:
        return df, info

    labels = discover_labels(df[text_col], extra_label_map)
    if not labels:
        return df, info

    extra = {_label_key(k): v for k, v in (extra_label_map or {}).items()}
    allowed = set(CANONICAL_FIELDS)

    # slug -> destination columns (a label can feed a canonical field *and* keep
    # its own note: column).
    wanted: dict[str, list[str]] = {}
    for label in labels:
        slug = _label_key(label)
        targets: list[str] = []

        known = NOTE_LABEL_ALIASES.get(slug)
        guessed = extra.get(slug)
        canonical = known or guessed
        if canonical in allowed:
            info["canonical"][label] = canonical
            targets.append(canonical)

        if known is None:
            note_col = f"{NOTE_PREFIX}{label}"
            info["note_columns"].append(note_col)
            targets.append(note_col)

        wanted[slug] = targets

    info["labels"] = labels

    out = df.copy()
    extracted = out[text_col].map(lambda text: _extract_row(text, wanted))

    for target in dict.fromkeys(t for targets in wanted.values() for t in targets):
        values = extracted.map(lambda found, t=target: found.get(t, pd.NA))
        values = values.map(lambda v: deslug(v) if isinstance(v, str) else v)

        if target.startswith(NOTE_PREFIX) or target not in out.columns:
            out[target] = values
        else:
            # Align dtype before fillna: pandas types an all-empty Excel column
            # as float64, which will not accept string fills.
            out[target] = out[target].astype(object).fillna(values)

    return out, info

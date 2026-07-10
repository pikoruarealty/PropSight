"""Ask an LLM how to map an unrecognized export's columns onto our schema.

The alias table in `normalize.py` covers the three formats we have seen. When a
fourth arrives — a new CRM, a new agency's spreadsheet — its headers match
nothing and the sheet is discarded as "not lead data", which is the worst
possible failure: the user uploaded leads and the app shrugged.

This module is the fallback. It sends the LLM the column names plus a small
sample of rows (the values are what disambiguate `Status` the call status from
`Status` the deal stage) and asks for a column -> canonical-field mapping.

Two properties matter more than mapping quality:

  * It never runs unless the deterministic aliases have already failed, so a
    known format is never at the mercy of a model's mood.
  * Its output is validated against CANONICAL_FIELDS before use. A hallucinated
    target field is dropped rather than propagated into the DataFrame.

The sampled rows contain real customer names and phone numbers, so this sends
personal data to Groq. It is opt-in via GROQ_API_KEY, and the sample is capped.
"""

from __future__ import annotations

import json

import pandas as pd

from ..analytics import llm
from .normalize import CANONICAL_FIELDS, matched_canonical_count, normalize_headers

SAMPLE_ROWS = 10
MAX_CELL_CHARS = 60

# Fields an inferred *note label* may never be routed to.
#
# `qualitative_remarks` is the column the labels were extracted from: mapping a
# label back onto it is self-referential and would overwrite the source text.
# The identity fields are already covered exhaustively by the static alias table,
# so a model rerouting them can only do damage.
UNSAFE_NOTE_TARGETS = frozenset({"qualitative_remarks", "name", "email", "phone"})

_PROMPT = """\
You are mapping a spreadsheet of real-estate sales leads onto a fixed schema.

TARGET FIELDS (map to these exact names, or omit the column entirely):
{fields}

Field meanings that are easy to confuse:
- "name": the lead/customer's own name. Never a company, project, or salesperson name.
- "phone": the lead's mobile number.
- "date": when the lead was created or received.
- "stage": where the lead sits in the pipeline (New, Assigned, Qualified...).
- "call_status": the outcome of phoning them (Spoken, Not Spoken, Busy...).
- "buying_status": how close they are to purchasing (Still Searching, Bought already...).
- "budget": how much they intend to spend, free text ("5 Cr", "80 lakh").
- "configuration_required": the unit type wanted (3BHK, 4BHK, Bungalow, Plot).
- "hwc": a priority/quality marker; any non-empty value means "important client".
- "qualitative_remarks": free-text notes written about the lead.

Here are the columns of an unrecognized export, with {n} sample rows:

{table}

Return ONLY a JSON object mapping source column name -> target field name.
Omit any column that does not clearly correspond to a target field.
Do not invent target field names. Do not map two columns to the same field.

Example: {{"Cust Name": "name", "Mob No": "phone", "Reqmt": "configuration_required"}}
"""


def _sample_table(df: pd.DataFrame) -> str:
    """Compact JSON preview: column names plus a handful of representative values.

    Rows with the most non-null cells are chosen; a sample of mostly-empty rows
    would tell the model nothing about what a column contains.
    """
    head = df.assign(_filled=df.notna().sum(axis=1)).nlargest(SAMPLE_ROWS, "_filled")
    head = head.drop(columns="_filled")

    preview = {}
    for col in df.columns:
        values = []
        for value in head[col].tolist():
            if pd.isna(value):
                continue
            text = str(value).strip()
            if text:
                values.append(text[:MAX_CELL_CHARS])
        preview[str(col)] = values[:SAMPLE_ROWS]
    return json.dumps(preview, indent=1, default=str)


# The column *names* decide the mapping; the sampled rows only disambiguate
# what a column means, and mean the same thing regardless of which rows were
# sampled. This matters because one upload splits a single sheet into up to
# a dozen virtual sheets by campaign (`ingest_service._campaign_groups`) —
# same columns, different leads in each. Keying on columns alone means only
# the first split pays for a Groq round-trip; without it, one Privyr sheet
# with a dozen campaigns fired a dozen serial network calls before parsing
# even finished.
_MAPPING_CACHE: dict[tuple, dict[str, str]] = {}


def infer_column_mapping(df: pd.DataFrame) -> dict[str, str]:
    """Return {source_column: canonical_field} for an unrecognized sheet.

    Returns {} when the LLM is disabled, unreachable, or returns nothing usable.
    """
    if not llm.is_enabled() or df.empty:
        return {}

    cache_key = tuple(str(c) for c in df.columns)
    if cache_key in _MAPPING_CACHE:
        return _MAPPING_CACHE[cache_key]

    prompt = _PROMPT.format(
        fields="\n".join(f"- {f}" for f in CANONICAL_FIELDS),
        n=min(SAMPLE_ROWS, len(df)),
        table=_sample_table(df),
    )
    raw = llm.chat_json(prompt, max_tokens=1024, temperature=0.0)
    if not isinstance(raw, dict):
        _MAPPING_CACHE[cache_key] = {}
        return {}

    source_columns = {str(c) for c in df.columns}
    allowed = set(CANONICAL_FIELDS)

    mapping: dict[str, str] = {}
    claimed: set[str] = set()
    for source, target in raw.items():
        source, target = str(source), str(target)
        # Reject hallucinated columns, hallucinated fields, and double-mapping.
        if source not in source_columns or target not in allowed or target in claimed:
            continue
        mapping[source] = target
        claimed.add(target)
    _MAPPING_CACHE[cache_key] = mapping
    return mapping


def clear_mapping_cache() -> None:
    """Drop every cached LLM mapping decision. Exists for tests."""
    _MAPPING_CACHE.clear()
    _NOTE_MAPPING_CACHE.clear()


def apply_mapping(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    """Rename mapped columns; leave everything else untouched."""
    return df.rename(columns=mapping) if mapping else df


# A label's wording is what decides its meaning, not which sheet it showed up
# on: the same ad-form question recurs across every sheet of a multi-sheet
# workbook. Keyed on the label set alone (not the sampled answers), so a
# question already resolved on an earlier sheet skips the network entirely.
_NOTE_MAPPING_CACHE: dict[tuple, dict[str, str]] = {}


def infer_note_label_mapping(df: pd.DataFrame) -> dict[str, str]:
    """Map ad-form questions found inside the notes column onto canonical fields.

    A label like 'What Budget Are You Comfortable With?' is a budget question no
    static alias table will ever fully enumerate — every ad set words it
    differently. The labels and their answers are shaped exactly like columns and
    their values, so they go through the same validated mapper.

    Only labels the static table does not already recognize are sent. Labels that
    map nowhere are not a failure: `extract_note_fields` keeps them as `note:`
    columns, which the user can still sort on.
    """
    if not llm.is_enabled():
        return {}


    # Imported here: `notes` imports this module for nothing else, and a
    # top-level import would be circular.
    from .notes import NOTE_LABEL_ALIASES, _label_key, _pairs, _text_column, count_labels

    text_col = _text_column(df)
    if text_col is None:
        return {}

    labels = count_labels(df[text_col])
    unknown = [lbl for lbl in labels if _label_key(lbl) not in NOTE_LABEL_ALIASES]
    if not unknown:
        return {}

    cache_key = tuple(sorted(unknown))
    if cache_key in _NOTE_MAPPING_CACHE:
        return _NOTE_MAPPING_CACHE[cache_key]

    # Present each label as a column of its observed answers.
    samples: dict[str, list[str]] = {lbl: [] for lbl in unknown}
    wanted = {_label_key(lbl): lbl for lbl in unknown}
    for text in df[text_col].dropna():
        for label, value in _pairs(text):
            target = wanted.get(_label_key(label))
            if target and len(samples[target]) < SAMPLE_ROWS:
                samples[target].append(value)

    width = max((len(v) for v in samples.values()), default=0)
    if not width:
        _NOTE_MAPPING_CACHE[cache_key] = {}
        return {}
    padded = {k: v + [None] * (width - len(v)) for k, v in samples.items()}
    mapping = infer_column_mapping(pd.DataFrame(padded))
    result = {k: v for k, v in mapping.items() if v not in UNSAFE_NOTE_TARGETS}
    _NOTE_MAPPING_CACHE[cache_key] = result
    return result


# Below this shape, a sheet is not a candidate lead export in the first place —
# it is the "Summary"/"Notes"/pivot tab every real workbook carries alongside
# its lead sheets. Those have zero alias matches by definition (there is no
# lead data to match), so without this gate every one of them still paid for
# a full Groq round-trip on every single upload, for data that could never
# have come back mapped.
MIN_COLUMNS_FOR_ESCALATION = 4
MIN_ROWS_FOR_ESCALATION = 2


def normalize_with_llm_fallback(df: pd.DataFrame, min_matches: int = 5) -> tuple[pd.DataFrame, dict]:
    """Normalize headers, escalating to the LLM only if the alias table falls short.

    Returns (normalized_df, info) where info records whether the LLM was used
    and what it decided, so the upload UI can show the user the inferred mapping
    instead of applying it invisibly.
    """
    normalized = normalize_headers(df)
    matched = matched_canonical_count(normalized)
    info: dict = {"llm_used": False, "matched_fields": matched, "mapping": {}}

    if matched >= min_matches:
        return normalized, info
    if len(df.columns) < MIN_COLUMNS_FOR_ESCALATION or len(df) < MIN_ROWS_FOR_ESCALATION:
        return normalized, info

    mapping = infer_column_mapping(df)
    if not mapping:
        return normalized, info

    # Map the raw frame, then re-run the alias pass so LLM-named columns and
    # alias-recognized ones go through identical collision handling.
    remapped = normalize_headers(apply_mapping(df, mapping))
    info.update(
        llm_used=True,
        mapping=mapping,
        matched_fields=matched_canonical_count(remapped),
    )
    return remapped, info

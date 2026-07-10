"""Indian mobile-number normalization, validation, and identity keys.

Phone is the only field shared reliably across every export format we see
(legacy calling sheets, Privyr, the CRM CSV), so it is the join key used to
recognize the same lead across files. That makes normalization load-bearing:
a number that fails to normalize can neither be deduplicated nor matched.

Real-world dirt observed in the client's exports, and how each is handled:

    +919825042217       country code               -> 9825042217
    +9109825171429      country code + trunk '0'   -> 9825171429
    +19737364488        '+91' mistyped as '+1'     -> 9737364488
    09824039892         leading trunk '0'          -> 9824039892
    9099903250, 982...  two numbers in one cell    -> first one wins
    9824233538.0        Excel read the cell as a float
    1524279386          10 digits, bad prefix      -> invalid
    '20-0010511'        not a phone                -> invalid
    '#REF!'             spreadsheet error          -> invalid

A valid Indian mobile is exactly 10 digits beginning 6/7/8/9. Landlines and
foreign numbers are reported invalid: this dataset is an Ahmedabad
residential-property CRM, and every genuine lead is reachable on a mobile.
"""

from __future__ import annotations

import re

import pandas as pd

from .mojibake import fold_styled_letters

# Indian mobile: 10 digits, first digit 6-9.
_MOBILE = re.compile(r"^[6-9]\d{9}$")
# Splits a cell holding several numbers ("9099903250, 9824039892" / "... / ...").
_SPLIT = re.compile(r"[,;/|]| or ", re.IGNORECASE)


def _strip_country_code(digits: str) -> str:
    """Remove a leading 91/1 country code and any trunk zeros around it.

    Only strips when doing so can yield a 10-digit number, so a legitimate
    10-digit mobile starting with '91...' is never truncated.
    """
    digits = digits.lstrip("0")
    for code in ("91", "1"):
        if len(digits) > 10 and digits.startswith(code):
            candidate = digits[len(code):].lstrip("0")
            if len(candidate) == 10:
                return candidate
    return digits


def normalize_phone(value: object) -> str | None:
    """Return a bare 10-digit Indian mobile, or None if the cell holds no valid one.

    When a cell contains several numbers, the first valid one wins.
    """
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None

    raw = str(value).strip()
    if not raw:
        return None
    # Excel hands us numeric phone columns as floats: 9824233538.0
    if raw.endswith(".0"):
        raw = raw[:-2]

    for part in _SPLIT.split(raw):
        digits = re.sub(r"\D", "", part)
        if not digits:
            continue
        candidate = _strip_country_code(digits)
        if _MOBILE.match(candidate):
            return candidate
    return None


def phone_status(value: object) -> str:
    """Classify a raw phone cell as 'valid', 'invalid', or 'missing'.

    'missing' (blank cell) and 'invalid' (something is there but it is not a
    mobile number) are kept distinct: a blank is an incomplete record, while
    an invalid is corrupt data. They warrant different treatment on ingest.
    """
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return "missing"
    if not str(value).strip():
        return "missing"
    return "valid" if normalize_phone(value) else "invalid"


def normalize_phone_series(series: pd.Series) -> pd.Series:
    """Vectorized-ish normalize_phone over a column; result dtype is object."""
    return series.map(normalize_phone).astype(object)


_NAME_NOISE = re.compile(r"[^a-z]")
# Honorifics carry no identity and appear inconsistently across exports.
_TITLES = ("mr", "mrs", "ms", "dr", "shri", "smt", "miss")


def name_key(value: object) -> str:
    """Fold a name to a comparison key: lowercase, letters only, titles dropped.

    'Mr. Shravan Modi' and 'shravan  modi' both fold to 'shravanmodi', so the
    same person written two ways deduplicates. Returns '' for a missing name,
    which callers must treat as "unknown", never as "matches another blank".

    Styled Unicode is folded first, so a name typed as '𝑹𝒂𝒏𝒋𝒊𝒕' keys the same as
    the plain spelling instead of stripping to the empty string and being treated
    as nameless.
    """
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    text = fold_styled_letters(str(value)).strip().lower()
    if not text:
        return ""
    words = [w for w in re.split(r"\s+", text) if _NAME_NOISE.sub("", w) not in _TITLES]
    return _NAME_NOISE.sub("", "".join(words))

"""Repair text that was UTF-8 decoded as cp1252 and re-encoded as UTF-8.

The Privyr export does this to every non-ASCII character. A campaign named

    Bungalow Ahmd — OLD Goal (LEAD_GEN ₹80/lead)

arrives in the workbook as

    Bungalow Ahmd â€" OLD Goal (LEAD_GEN â‚¹80/lead)

because the em dash's UTF-8 bytes (e2 80 94) were read one byte at a time as
cp1252 ('â', '€', '"') and then re-encoded. The damage is invertible: encode the
mangled characters back to the bytes they came from, then decode those as UTF-8.

Two details make a naive implementation fail on this client's real data:

1. cp1252 leaves five byte values undefined (0x81 0x8D 0x8F 0x90 0x9D). Whatever
   mangled the text mapped them straight to U+0081…U+009D, so a strict cp1252
   encoder raises on them and the repair is abandoned. `_to_bytes` restores them.
   Without this, any character whose UTF-8 encoding contains one of those bytes —
   every 4-byte character, e.g. the styled name '𝑹𝒂𝒏𝒋𝒊𝒕' in row 2 — defeats it.

2. Repair must be attempted per *run* of damaged characters, not per cell. One
   irreparable run must not stop the em dash three words later from being fixed.
"""

from __future__ import annotations

import re
import unicodedata

import pandas as pd

# cp1252 never assigned these five bytes; the mangling passed them through as
# the matching C1 control codepoints.
_UNDEFINED = {"\x81": 0x81, "\x8d": 0x8d, "\x8f": 0x8f, "\x90": 0x90, "\x9d": 0x9d}


def _sloppy_decode(byte: int) -> str:
    char = bytes([byte])
    try:
        return char.decode("cp1252")
    except UnicodeDecodeError:
        return chr(byte)


# Every character a single byte >= 0x80 can turn into. A UTF-8 multi-byte
# sequence is made only of such bytes, so every mojibake character is in here.
_MOJIBAKE_CHARS = {_sloppy_decode(b) for b in range(0x80, 0x100)}

# A damaged run is 2+ consecutive suspect characters: the shortest UTF-8
# multi-byte sequence is two bytes. Requiring two keeps a lone legitimate 'é'
# or '—' from being touched.
_RUN = re.compile(f"[{re.escape(''.join(sorted(_MOJIBAKE_CHARS)))}]{{2,}}")


def _to_bytes(text: str) -> bytes | None:
    """Encode a suspect run back to the bytes it was mis-decoded from."""
    out = bytearray()
    for char in text:
        if char in _UNDEFINED:
            out.append(_UNDEFINED[char])
            continue
        try:
            out.extend(char.encode("cp1252"))
        except UnicodeEncodeError:
            return None
    return bytes(out)


def _repair_run(match: re.Match[str]) -> str:
    run = match.group(0)
    raw = _to_bytes(run)
    if raw is None:
        return run
    try:
        # Strict: a run that is not valid UTF-8 was never mojibake to begin with.
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return run


def repair_mojibake(text: str) -> str:
    """Undo one cp1252/UTF-8 round trip on every damaged run, or return as-is.

    Safe on clean input: a lone em dash is one suspect character, below the
    two-character minimum, and any run that does not decode as valid UTF-8 is
    left exactly as it was.
    """
    if not isinstance(text, str) or not text:
        return text
    return _RUN.sub(_repair_run, text)


def fold_styled_letters(text: str) -> str:
    """Map styled Unicode letters and digits to their plain ASCII equivalents.

    Leads type their name into an ad form using fancy characters — row 2 of the
    Privyr export is '𝑹𝒂𝒏𝒋𝒊𝒕 𝑽𝒂𝒈𝒉𝒆𝒍𝒂' in mathematical bold italic. Those are
    letters to a human but punctuation-to-be-stripped to `name_key`, so the lead
    never deduplicates and reaches Meta as an unmatchable string.

    A blanket NFKC pass is the obvious implementation and the wrong one: it also
    rewrites '¾' to '3⁄4' and 'ª' to 'a'. Requiring the result to be a single
    ASCII alphanumeric is still not enough — 'ª' and '¹' pass that test, and both
    occur inside mojibaked Gujarati names, where folding them invents a
    dedupe key for a name that is actually unreadable.

    So a character is folded only when it is a cased letter or a decimal digit
    (`Lu`/`Ll`/`Nd`) *and* NFKC maps it to one ASCII alphanumeric. That admits
    '𝑹' (Lu), 'Ａ' (Lu) and '９' (Nd), and rejects 'ª'/'º' (Lo), '¹'/'¾' (No),
    '₹', '—' and 'é'.

    This is a *matching* aid, applied where names are compared or exported. The
    stored cell keeps whatever the CRM actually holds.
    """
    if text.isascii():
        return text
    out = []
    for char in text:
        if unicodedata.category(char) in ("Lu", "Ll", "Nd"):
            folded = unicodedata.normalize("NFKC", char)
            if len(folded) == 1 and folded.isascii() and folded.isalnum():
                out.append(folded)
                continue
        out.append(char)
    return "".join(out)


def clean_text(text: str) -> str:
    """Repair mojibake. Text is otherwise stored exactly as the source held it."""
    if not isinstance(text, str) or not text:
        return text
    return repair_mojibake(text)


def is_text_column(series: pd.Series) -> bool:
    """True for columns that can hold Python strings.

    pandas 3 gives text columns a dedicated `str` dtype rather than `object`, so
    a bare `dtype == object` test silently skips every clean string column and
    only catches the mixed-type ones.
    """
    return pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)


def repair_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Clean every text cell and column header in a sheet."""
    out = df.copy()
    out.columns = [clean_text(c) if isinstance(c, str) else c for c in out.columns]
    for col in out.columns:
        if is_text_column(out[col]):
            out[col] = out[col].map(clean_text)
    return out

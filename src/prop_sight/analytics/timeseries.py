"""When do leads arrive? Day-of-week, hour-of-day, and volume over time.

Only some exports carry a usable timestamp, and only some of those carry a
*time* rather than a bare date:

    Privyr  'Date Created'   '2026-07-05 - 19:47'    date + time
    CRM CSV 'Received'       '23-Jun-26'             date only
    Legacy  'First Call Date' 2023-12-02 00:00:00    date only (Excel serial)

Hour-of-day is therefore computed only from rows that actually have a clock
time. Treating a date-only row as "midnight" would invent a spike at 00:00
roughly the size of the legacy dataset — the single most misleading thing this
module could do — so those rows are excluded from the hour and heatmap views
and counted separately.
"""

from __future__ import annotations

import pandas as pd

from .common import to_jsonable

DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# A weekday x hour heatmap has 168 cells. Drawing it from a handful of rows
# produces a grid of near-empty cells whose one dark square looks like a finding
# and is noise. Below this many timestamped leads, the hourly views are withheld.
MIN_TIMED_LEADS = 30

# Privyr writes '2026-07-05 - 19:47'; the ' - ' separator defeats pd.to_datetime's
# inference, so it is tried explicitly before falling back to free-form parsing.
_EXPLICIT_FORMATS = ["%Y-%m-%d - %H:%M", "%Y-%m-%d - %H:%M:%S", "%d-%b-%y", "%d %b %Y, %I:%M %p"]


def parse_datetime_series(series: pd.Series) -> pd.Series:
    """Parse a mixed-format date column to datetime64, unparseable rows -> NaT."""
    if series is None or series.empty:
        return pd.Series(pd.NaT, index=getattr(series, "index", None), dtype="datetime64[ns]")

    if pd.api.types.is_datetime64_any_dtype(series):
        return series

    text = series.astype("string").str.strip()
    text = text.mask(text.isin(["", "-", "nan", "None", "NA", "N/A"]))

    result = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    for fmt in _EXPLICIT_FORMATS:
        todo = result.isna() & text.notna()
        if not todo.any():
            break
        parsed = pd.to_datetime(text[todo], format=fmt, errors="coerce")
        result.loc[todo] = parsed

    todo = result.isna() & text.notna()
    if todo.any():
        parsed = pd.to_datetime(text[todo], errors="coerce", dayfirst=True, format="mixed")
        result.loc[todo] = parsed
    return result


def _lead_timestamps(df: pd.DataFrame) -> pd.Series:
    """Best available creation timestamp per lead, preferring the truest source.

    'date' is when the lead entered the system. 'first_call_date' is when
    someone got round to it — a proxy that is usually the same day, and the
    only signal the legacy sheets have.
    """
    for col in ("date", "first_call_date", "last_activity"):
        if col in df.columns:
            parsed = parse_datetime_series(df[col])
            if parsed.notna().any():
                return parsed
    return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")


def _has_clock_time(stamps: pd.Series) -> pd.Series:
    """True where the timestamp carries a real time, not an implied midnight."""
    valid = stamps.notna()
    non_midnight = (stamps.dt.hour != 0) | (stamps.dt.minute != 0)
    return valid & non_midnight


def time_analysis(df: pd.DataFrame) -> dict:
    """Lead arrival patterns by weekday, hour, month, and weekday x hour.

    Returns `available: False` when no column in the frame parses to a date at
    all, so the dashboard can hide the section rather than render empty axes.
    """
    stamps = _lead_timestamps(df)
    dated = stamps.dropna()

    result: dict = {
        "available": bool(len(dated)),
        "dated_leads": int(len(dated)),
        "total_leads": int(len(df)),
    }
    if dated.empty:
        return to_jsonable(result)

    by_day = dated.dt.day_name().value_counts().reindex(DAY_ORDER, fill_value=0)
    result["by_weekday"] = [{"label": d, "count": int(by_day[d])} for d in DAY_ORDER]

    months = dated.dt.to_period("M").value_counts().sort_index()
    result["by_month"] = [
        {"label": str(period), "count": int(count)} for period, count in months.items()
    ]

    result["range"] = {"start": dated.min().isoformat(), "end": dated.max().isoformat()}

    # Hour-of-day only for rows with a genuine clock time.
    timed = stamps[_has_clock_time(stamps)]
    result["timed_leads"] = int(len(timed))
    result["has_time_of_day"] = len(timed) >= MIN_TIMED_LEADS
    result["min_timed_leads"] = MIN_TIMED_LEADS

    if result["has_time_of_day"]:
        hours = timed.dt.hour.value_counts().reindex(range(24), fill_value=0)
        result["by_hour"] = [
            {"label": f"{h:02d}:00", "hour": int(h), "count": int(hours[h])} for h in range(24)
        ]
        # weekday x hour, as a dense 7x24 matrix for a heatmap.
        frame = pd.DataFrame({"day": timed.dt.day_name(), "hour": timed.dt.hour})
        grid = (
            pd.crosstab(frame["day"], frame["hour"])
            .reindex(index=DAY_ORDER, fill_value=0)
            .reindex(columns=range(24), fill_value=0)
        )
        result["weekday_x_hour"] = {
            "rows": DAY_ORDER,
            "cols": [f"{h:02d}" for h in range(24)],
            "matrix": [[int(v) for v in row] for row in grid.values],
        }

        peak_hour = int(hours.idxmax())
        result["peak"] = {
            "weekday": str(by_day.idxmax()),
            "weekday_count": int(by_day.max()),
            "hour": peak_hour,
            "hour_label": f"{peak_hour:02d}:00–{(peak_hour + 1) % 24:02d}:00",
            "hour_count": int(hours.max()),
        }
    else:
        result["peak"] = {"weekday": str(by_day.idxmax()), "weekday_count": int(by_day.max())}

    return to_jsonable(result)

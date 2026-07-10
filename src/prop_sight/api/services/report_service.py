"""Report lifecycle: confirm (build + cache), read, filter, discard, insights."""

from __future__ import annotations

from ...analytics import rules
from ...analytics.chart_summaries import generate_chart_summaries
from ...analytics.core import hwc_flag
from ...analytics.llm_insights import generate_insights
from ...analytics.report import classify_frame, full_report
from ...ingestion.dedupe import dedupe_leads
from ...ingestion.merge import merge_sheets
from ..state import (
    COMBINED_ID,
    REPORTS,
    REPORTS_LOCK,
    combined_entry,
    current_rules,
    delete_report_from_disk,
    invalidate_combined,
    ready_reports,
    save_report,
)


def get_entry(report_id: str) -> dict | None:
    """Resolve a report id, including the virtual combined-pool id."""
    if report_id == COMBINED_ID:
        return combined_entry()
    return REPORTS.get(report_id)


def has_any_data() -> bool:
    return bool(ready_reports())


def confirm_report(report_id: str, sheets_by_index: dict[int, dict]) -> dict:
    """Apply confirmed per-sheet include/type, merge included sheets, build + cache."""
    with REPORTS_LOCK:
        entry = REPORTS.get(report_id)
        if entry is None:
            raise KeyError(report_id)

        parsed = entry["parsed"]
        included = []
        for index in range(len(parsed)):
            choice = sheets_by_index.get(index, {})
            include = bool(choice.get("include", parsed[index]["include"]))
            parsed[index]["include"] = include
            if not include:
                continue
            ptype = str(choice.get("property_type", "") or "").strip()
            if not ptype:
                raise ValueError(
                    f"Sheet '{parsed[index]['sheet_name']}' in "
                    f"'{parsed[index]['filename']}' has no property type selected."
                )
            parsed[index]["property_type"] = ptype
            included.append(parsed[index])

        if not included:
            raise ValueError("No sheets selected — include at least one lead sheet.")

        df = merge_sheets(
            [
                {
                    "filename": p["filename"],
                    "sheet_name": p["sheet_name"],
                    "property_type": p["property_type"],
                    "df": p["df"],
                    "column_mapping": p.get("column_mapping"),
                }
                for p in included
            ]
        )
        # Deduplicate within the upload. The cross-upload pass happens again in
        # `combined_entry`, where leads shared between files can be matched.
        df, stats = dedupe_leads(df)

        entry["df"] = df
        entry["report"] = full_report(df, current_rules())
        entry["dedupe_stats"] = stats
        entry["insights_cache"] = None
        entry["summaries_cache"] = None
        entry["status"] = "ready"

    persisted = save_report(report_id)
    return {
        "report_id": report_id,
        "status": "ready",
        "total_leads": int(len(df)),
        "dedupe": stats,
        "persisted": persisted,
    }


def get_report_data(
    report_id: str,
    property_type: str | None = None,
    segment: str | None = None,
    source_file: str | None = None,
) -> dict | None:
    """Cached full report, or a re-computed slice when filters are applied.

    segment: 'hwc' (any value in the HWC column) — the only supported focus filter.
    """
    entry = get_entry(report_id)
    if entry is None or entry["status"] != "ready":
        return None

    no_type = not property_type or property_type.lower() == "all"
    no_segment = not segment or segment.lower() == "all"
    no_file = not source_file or source_file.lower() == "all"

    if no_type and no_segment and no_file:
        report = dict(entry["report"])
    else:
        rule_list = current_rules()
        df = entry["df"]
        if not no_type:
            df = df[df["property_type"] == property_type]
        if not no_file:
            df = df[df["source_file"] == source_file]
        if not no_segment:
            df = _apply_segment(df, segment.lower(), rule_list)
        report = full_report(df, rule_list)

    report["dedupe"] = entry.get("dedupe_stats")
    report["is_combined"] = report_id == COMBINED_ID
    return report


def _apply_segment(df, segment: str, rule_list: list[dict]):
    """Narrow the frame to one focus segment.

    'good'/'bad' are derived columns, not stored ones, so the frame is prepared
    and classified exactly as `full_report` would before the mask is taken —
    otherwise the segment would be computed against unfolded labels and a
    `budget_bucket` column that does not exist yet.
    """
    if segment == "hwc":
        return df[hwc_flag(df)]
    if segment in rules.CATEGORIES:
        if not rule_list:
            return df.iloc[0:0]
        classified = classify_frame(df, rule_list)
        return df[(classified[rules.CLASS_COL] == segment).to_numpy()]
    return df


def get_insights(report_id: str) -> dict | None:
    """Groq AI insights, cached per report in memory (never on disk)."""
    entry = get_entry(report_id)
    if entry is None or entry["status"] != "ready":
        return None
    cached = entry["insights_cache"]
    # A rate-limit hit is transient — don't let it stick around until the next
    # report rebuild, or the user is stuck seeing it long after the limit clears.
    if cached is None or cached.get("retryable"):
        cached = entry["insights_cache"] = generate_insights(entry["report"], entry["df"])
    return cached


def get_chart_summaries(report_id: str) -> dict | None:
    """Plain-English caption per cross-tab chart. Falls back to static text."""
    entry = get_entry(report_id)
    if entry is None or entry["status"] != "ready":
        return None
    if entry.get("summaries_cache") is None:
        entry["summaries_cache"] = generate_chart_summaries(entry["report"])
    return entry["summaries_cache"]


def list_reports() -> list[dict]:
    with REPORTS_LOCK:
        items = list(REPORTS.items())
    out = [
        {
            "report_id": report_id,
            "status": entry["status"],
            "created_at": entry["created_at"],
            "files": entry["files"],
            "row_count": (
                int(len(entry["df"]))
                if entry["df"] is not None
                else int(sum(len(p["df"]) for p in entry["parsed"]))
            ),
        }
        for report_id, entry in items
    ]
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return out


def discard_report(report_id: str) -> bool:
    if report_id == COMBINED_ID:
        return False
    delete_report_from_disk(report_id)
    with REPORTS_LOCK:
        removed = REPORTS.pop(report_id, None) is not None
    invalidate_combined()
    return removed

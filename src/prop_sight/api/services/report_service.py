"""Report lifecycle: confirm (build + cache), read, filter, discard, insights."""

from __future__ import annotations

from ...analytics.core import hwc_flag
from ...analytics.llm_insights import generate_insights
from ...analytics.report import full_report
from ...ingestion.merge import merge_sheets
from ..state import REPORTS, REPORTS_LOCK


def get_entry(report_id: str) -> dict | None:
    return REPORTS.get(report_id)


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
                }
                for p in included
            ]
        )
        entry["df"] = df
        entry["report"] = full_report(df)
        entry["insights_cache"] = None
        entry["status"] = "ready"
    return {"report_id": report_id, "status": "ready", "total_leads": len(df)}


def get_report_data(
    report_id: str,
    property_type: str | None = None,
    segment: str | None = None,
    source_file: str | None = None,
) -> dict | None:
    """Cached full report, or a re-computed slice when filters are applied.

    segment: 'hwc' (any value in the HWC column) — the only supported focus filter.
    """
    entry = REPORTS.get(report_id)
    if entry is None or entry["status"] != "ready":
        return None
    no_type = not property_type or property_type.lower() == "all"
    no_segment = not segment or segment.lower() == "all"
    no_file = not source_file or source_file.lower() == "all"
    
    if no_type and no_segment and no_file:
        return entry["report"]
    df = entry["df"]
    if not no_type:
        df = df[df["property_type"] == property_type]
    if not no_file:
        df = df[df["source_file"] == source_file]
    if not no_segment:
        seg = segment.lower()
        if seg == "hwc":
            df = df[hwc_flag(df)]
    return full_report(df)


def get_insights(report_id: str) -> dict | None:
    """Groq AI insights, cached per report in memory (never on disk)."""
    entry = REPORTS.get(report_id)
    if entry is None or entry["status"] != "ready":
        return None
    if entry["insights_cache"] is None:
        # Pass the full DataFrame so remarks can be sampled
        entry["insights_cache"] = generate_insights(entry["report"], entry["df"])
    return entry["insights_cache"]


def list_reports() -> list[dict]:
    with REPORTS_LOCK:
        items = list(REPORTS.items())
    out = []
    for report_id, entry in items:
        out.append(
            {
                "report_id": report_id,
                "status": entry["status"],
                "created_at": entry["created_at"],
                "files": entry["files"],
                "row_count": (
                    int(len(entry["df"])) if entry["df"] is not None
                    else int(sum(len(p["df"]) for p in entry["parsed"]))
                ),
            }
        )
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return out


def discard_report(report_id: str) -> bool:
    with REPORTS_LOCK:
        return REPORTS.pop(report_id, None) is not None

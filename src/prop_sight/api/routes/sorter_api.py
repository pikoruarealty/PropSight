"""Rule editing, classification preview, and classified-lead downloads."""

from __future__ import annotations

import urllib.parse

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import Response

from ...analytics import rules as rules_engine
from ...analytics.report import classify_frame
from ..services import export_service, report_service, rules_service
from ..state import rebuild_reports

router = APIRouter()

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _entry_or_404(report_id: str) -> dict:
    entry = report_service.get_entry(report_id)
    if entry is None or entry["status"] != "ready":
        raise HTTPException(404, "Report not found or not confirmed yet.")
    return entry


def _attachment(content: bytes, filename: str, media_type: str) -> Response:
    # RFC 5987: the filename can carry non-ASCII (a sheet name, a campaign).
    quoted = urllib.parse.quote(filename)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )


@router.get("/api/rules")
def get_rules():
    return {"rules": rules_service.load_rules(), "operators": rules_engine.OPERATORS}


@router.put("/api/rules")
def put_rules(payload: dict = Body(...)):
    """Replace the rule set, then rebuild every cached report against it."""
    try:
        saved = rules_service.save_rules(payload.get("rules", []))
    except rules_engine.RuleError as exc:
        raise HTTPException(422, str(exc))

    rebuild_reports()
    return {"rules": saved, "count": len(saved)}


@router.get("/api/reports/{report_id}/fields")
def get_fields(report_id: str):
    """Fields a rule can target, with their distinct values."""
    entry = _entry_or_404(report_id)
    return {"fields": rules_service.field_catalogue(entry["df"])}


@router.get("/api/reports/{report_id}/classification")
def get_classification(report_id: str, preview_limit: int = Query(10, ge=0, le=100)):
    """Counts, unclassified reasons, and a preview of the good leads."""
    entry = _entry_or_404(report_id)
    rule_list = rules_service.load_rules()
    classified = classify_frame(entry["df"], rule_list)

    summary = rules_engine.classification_summary(classified)
    good = classified[classified[rules_engine.CLASS_COL] == rules_engine.GOOD]

    preview_cols = [
        c
        for c in ("name", "phone", "budget", "configuration_required", "buying_status", "call_status")
        if c in good.columns
    ]
    preview = (
        good[preview_cols].head(preview_limit).fillna("").astype(str).to_dict("records")
        if preview_cols and len(good)
        else []
    )

    return {
        "summary": summary,
        "columns": export_service.exportable_columns(entry["df"]),
        "preview_columns": preview_cols,
        "preview": preview,
        "rule_count": len(rule_list),
    }


@router.get("/api/reports/{report_id}/export/{category}.xlsx")
def export_class(report_id: str, category: str, columns: str | None = None):
    entry = _entry_or_404(report_id)
    try:
        rows = export_service.classified_slice(
            entry["df"], rules_service.load_rules(), category
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))

    if rows.empty:
        raise HTTPException(404, f"No {category} leads to export.")

    wanted = [c for c in (columns or "").split(",") if c] or None
    return _attachment(
        export_service.to_excel(rows, wanted), f"{category}_leads.xlsx", _XLSX_MIME
    )


@router.get("/api/reports/{report_id}/export/meta-audience.csv")
def export_meta_audience(report_id: str):
    """Meta customer-list CSV built from the good leads."""
    entry = _entry_or_404(report_id)
    rows = export_service.classified_slice(
        entry["df"], rules_service.load_rules(), rules_engine.GOOD
    )
    if rows.empty:
        raise HTTPException(404, "No good leads to export.")

    return _attachment(
        export_service.to_meta_audience_csv(rows), "meta_audience.csv", "text/csv; charset=utf-8"
    )

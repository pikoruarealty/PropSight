"""JSON data endpoints consumed by the dashboard JS."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import report_service

router = APIRouter()


@router.get("/reports/{report_id}/data")
def get_report_json(
    report_id: str,
    property_type: str | None = None,
    segment: str | None = None,
    source_file: str | None = None,
):
    data = report_service.get_report_data(report_id, property_type, segment, source_file)
    if not data:
        raise HTTPException(404, "Report not found or not ready")
    return data


@router.get("/reports/{report_id}/insights")
def report_insights(report_id: str):
    data = report_service.get_insights(report_id)
    if data is None:
        raise HTTPException(404, "Report not found or not confirmed yet.")
    return data

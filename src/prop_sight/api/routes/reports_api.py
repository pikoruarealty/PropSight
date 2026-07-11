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
    budget_min: float | None = None,
    budget_max: float | None = None,
    configuration: str | None = None,
    call_status: str | None = None,
    buying_status: str | None = None,
    hwc_only: bool = False,
):
    data = report_service.get_report_data(
        report_id,
        property_type,
        segment,
        source_file,
        budget_min=budget_min,
        budget_max=budget_max,
        configuration=report_service.csv_list(configuration),
        call_status=report_service.csv_list(call_status),
        buying_status=report_service.csv_list(buying_status),
        hwc_only=hwc_only,
    )
    if not data:
        raise HTTPException(404, "Report not found or not ready")
    return data


@router.get("/reports/{report_id}/insights")
def report_insights(report_id: str):
    data = report_service.get_insights(report_id)
    if data is None:
        raise HTTPException(404, "Report not found or not confirmed yet.")
    return data


@router.get("/reports/{report_id}/chart-summaries")
def report_chart_summaries(report_id: str):
    """Plain-English caption per cross-tab chart (static text when the LLM is off)."""
    data = report_service.get_chart_summaries(report_id)
    if data is None:
        raise HTTPException(404, "Report not found or not confirmed yet.")
    return {"summaries": data}

"""Server-rendered pages."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import TEMPLATES_DIR
from ..services import report_service

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/", response_class=HTMLResponse)
def home() -> RedirectResponse:
    return RedirectResponse("/upload")


@router.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request):
    return templates.TemplateResponse(request, "upload.html", {})


@router.get("/reports", response_class=HTMLResponse)
def reports_list(request: Request):
    return templates.TemplateResponse(
        request, "reports_list.html", {"reports": report_service.list_reports()}
    )


@router.get("/reports/{report_id}", response_class=HTMLResponse)
def report_dashboard(request: Request, report_id: str):
    entry = report_service.get_entry(report_id)
    if entry is None:
        return templates.TemplateResponse(
            request, "reports_list.html",
            {"reports": report_service.list_reports(), "error": "Report not found — it may have been discarded or the server restarted."},
            status_code=404,
        )
    if entry["status"] != "ready":
        return RedirectResponse("/upload")
    return templates.TemplateResponse(request, "dashboard.html", {"report_id": report_id})

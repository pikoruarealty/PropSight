"""Server-rendered pages. Every one of them requires a signed-in user."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import page_user
from ..config import TEMPLATES_DIR
from ..services import report_service
from ..state import COMBINED_ID

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _pretty_datetime(value: str) -> str:
    """`created_at` is stored as an ISO string. Show a person a date, not a timestamp."""
    try:
        return dt.datetime.fromisoformat(value).strftime("%d %b %Y, %H:%M")
    except (TypeError, ValueError):
        return value or "—"


templates.env.filters["pretty_datetime"] = _pretty_datetime


@router.get("/", response_class=HTMLResponse)
def home(user: dict = Depends(page_user)):
    """Land on the combined dashboard once any leads exist, else on upload.

    The uploads are different export formats of one lead book, so the union of
    them — not any individual file — is the thing worth looking at first.
    """
    if report_service.has_any_data():
        return RedirectResponse(f"/reports/{COMBINED_ID}")
    return RedirectResponse("/upload")


@router.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request, user: dict = Depends(page_user)):
    return templates.TemplateResponse(request, "upload.html", {"user": user})


@router.get("/datasets", response_class=HTMLResponse)
def datasets(request: Request, user: dict = Depends(page_user)):
    return templates.TemplateResponse(
        request,
        "datasets.html",
        {
            "user": user,
            "reports": report_service.list_reports(),
            "has_combined": report_service.has_any_data(),
        },
    )


@router.get("/reports", response_class=HTMLResponse)
def reports_list_legacy():
    """The page was renamed to /datasets; keep old bookmarks working."""
    return RedirectResponse("/datasets", status_code=308)


@router.get("/sorter", response_class=HTMLResponse)
def sorter_page(request: Request, user: dict = Depends(page_user)):
    """Rule editor + exports, always over the combined pool.

    Rules describe what a good lead is for this business, so they are global.
    Sorting one upload in isolation would only invite a different rule set per
    file — the exact trap the standalone tool fell into.
    """
    if not report_service.has_any_data():
        return RedirectResponse("/upload")
    return templates.TemplateResponse(
        request, "sorter.html", {"user": user, "report_id": COMBINED_ID}
    )


@router.get("/reports/{report_id}", response_class=HTMLResponse)
def report_dashboard(request: Request, report_id: str, user: dict = Depends(page_user)):
    entry = report_service.get_entry(report_id)
    if entry is None:
        return templates.TemplateResponse(
            request,
            "datasets.html",
            {
                "user": user,
                "reports": report_service.list_reports(),
                "has_combined": report_service.has_any_data(),
                "error": "Dataset not found — it may have been discarded or the server restarted.",
            },
            status_code=404,
        )
    if entry["status"] != "ready":
        return RedirectResponse("/upload")
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "report_id": report_id, "is_combined": report_id == COMBINED_ID},
    )

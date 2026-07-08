"""Upload + confirm + discard endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from ..config import MAX_FILES_PER_UPLOAD, MAX_UPLOAD_BYTES
from ..services import ingest_service, report_service

router = APIRouter()


@router.post("/upload")
async def upload_workbooks(files: list[UploadFile]):
    if not files:
        raise HTTPException(400, "No files uploaded.")
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(400, f"Too many files (max {MAX_FILES_PER_UPLOAD}).")
    payload: list[tuple[str, bytes]] = []
    for f in files:
        name = f.filename or "unnamed.xlsx"
        is_xlsx = name.lower().endswith(".xlsx")
        is_csv = name.lower().endswith(".csv")
        if not (is_xlsx or is_csv):
            raise HTTPException(400, f"'{name}' is not an .xlsx or .csv file.")
        contents = await f.read()
        if len(contents) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, f"'{name}' exceeds the {MAX_UPLOAD_BYTES // (1024*1024)} MB limit.")
        payload.append((name, contents))
    try:
        return ingest_service.create_draft(payload)
    except Exception as exc:
        raise HTTPException(422, f"Could not parse workbook: {exc}")


class SheetChoice(BaseModel):
    include: bool = True
    property_type: str = ""


class ConfirmBody(BaseModel):
    sheets: dict[int, SheetChoice]


@router.patch("/reports/{report_id}/confirm")
def confirm_report(report_id: str, body: ConfirmBody):
    choices = {index: choice.model_dump() for index, choice in body.sheets.items()}
    try:
        return report_service.confirm_report(report_id, choices)
    except KeyError:
        raise HTTPException(404, "Report not found.")
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.delete("/reports/{report_id}")
def discard_report(report_id: str):
    if not report_service.discard_report(report_id):
        raise HTTPException(404, "Report not found.")
    return {"discarded": report_id}

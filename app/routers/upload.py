# app/routers/upload.py
from fastapi import APIRouter, UploadFile, File, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.templates import templates
from app.services.weekly_reports import ingest_weekly_report
from app.models.weekly_report import WeeklyReport, WeeklyReportItem
from sqlalchemy import func

router = APIRouter()

ALLOWED_EXTENSION = ".xlsx"


def _build_upload_history(db: Session) -> list[dict]:
    """
    Читает все отчёты из БД, считает кол-во позиций для каждого,
    помечает актуальный (is_current) и предыдущий (is_previous).
    """
    # Получаем отчёты + кол-во позиций одним запросом
    rows = db.execute(
        select(
            WeeklyReport,
            func.count(WeeklyReportItem.id).label("row_count"),
        )
        .outerjoin(WeeklyReportItem, WeeklyReportItem.report_id == WeeklyReport.id)
        .group_by(WeeklyReport.id)
        .order_by(WeeklyReport.report_date.desc())
    ).all()

    history = []
    for i, (report, row_count) in enumerate(rows):
        history.append({
            "filename":    report.filename,
            "uploaded_at": report.upload_date.strftime("%d.%m.%Y %H:%M"),
            "row_count":   row_count,
            "status":      "ok",
            "is_current":  i == 0,
            "is_previous": i == 1,
        })
    return history


@router.get("/", response_class=HTMLResponse)
async def upload_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("upload.html", {
        "request":        request,
        "active_page":    "upload",
        "upload_history": _build_upload_history(db),
    })


@router.post("/")
async def upload_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith(ALLOWED_EXTENSION):
        raise HTTPException(400, detail=f"Ожидается файл {ALLOWED_EXTENSION}")

    contents = await file.read()
    ingest_weekly_report(filename=file.filename, file_bytes=contents, db=db)

    return {"status": "ok", "filename": file.filename}

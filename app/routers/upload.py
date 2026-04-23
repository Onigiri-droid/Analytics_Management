# app/routers/upload.py
from fastapi import APIRouter, UploadFile, File, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.templates import templates
from app.services.weekly_reports import ingest_weekly_report
from app.services.weekly_reports import infer_report_period_from_filename
from app.models.weekly_report import WeeklyReport, WeeklyReportItem
from sqlalchemy import func

router = APIRouter()

ALLOWED_EXTENSIONS = (".xlsx", ".xls")


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
    for report, row_count in rows:
        meta = infer_report_period_from_filename(report.filename or "")
        date_from_filename = meta["report_date"]
        sort_date = date_from_filename or report.report_date
        history.append({
            "filename":     report.filename,
            "uploaded_at":  report.upload_date.strftime("%d.%m.%Y %H:%M"),
            "report_date":  sort_date.strftime("%d.%m.%Y") if sort_date else "—",
            "row_count":    row_count,
            "period_label": meta["period_label"],
            "status":       "ok",
            "_sort_date":   sort_date,
        })

    history.sort(
        key=lambda x: (
            x["_sort_date"] is None,
            x["_sort_date"],
        ),
        reverse=True,
    )
    for i, item in enumerate(history):
        item["is_current"] = i == 0
        item["is_previous"] = i == 1
        item.pop("_sort_date", None)
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
    if not file.filename or not file.filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(
            400,
            detail=f"Ожидается файл формата: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    contents = await file.read()
    ingest_weekly_report(filename=file.filename, file_bytes=contents, db=db)

    return {"status": "ok", "filename": file.filename}

from fastapi import APIRouter, UploadFile, File, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.templates import templates
from app.services.weekly_reports import ingest_weekly_report

router = APIRouter()

ALLOWED_EXTENSION = ".xlsx"


@router.get("/", response_class=HTMLResponse)
async def upload_page(request: Request):
    return templates.TemplateResponse("upload.html", {"request": request})


@router.post("/")
async def upload_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith(ALLOWED_EXTENSION):
        raise HTTPException(400, detail=f"Ожидается файл {ALLOWED_EXTENSION}")

    contents = await file.read()

    result = ingest_weekly_report(filename=file.filename, file_bytes=contents, db=db)

    return result

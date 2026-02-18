from io import BytesIO

import pandas as pd
from fastapi import APIRouter, UploadFile, File, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.templates import templates

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
    df = pd.read_excel(BytesIO(contents))

    return {
        "filename": file.filename,
        "rows": len(df),
        "columns": list(df.columns),
    }

from fastapi import APIRouter

from app.routers import upload

api_router = APIRouter()
api_router.include_router(upload.router, prefix="/upload", tags=["upload"])

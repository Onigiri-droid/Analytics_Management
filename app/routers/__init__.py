from fastapi import APIRouter

from app.routers import analytics, upload, dashboard

api_router = APIRouter()
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(upload.router, prefix="/upload", tags=["upload"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])

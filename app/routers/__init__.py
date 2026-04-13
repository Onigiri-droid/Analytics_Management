# app/routers/__init__.py
from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.routers import analytics, auth, dashboard, orders, upload

api_router = APIRouter()
api_router.include_router(auth.router,       prefix="/auth",       tags=["auth"])
api_router.include_router(
    dashboard.router,
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(get_current_user)],
)
api_router.include_router(
    upload.router,
    prefix="/upload",
    tags=["upload"],
    dependencies=[Depends(get_current_user)],
)
api_router.include_router(
    analytics.router,
    prefix="/analytics",
    tags=["analytics"],
    dependencies=[Depends(get_current_user)],
)
api_router.include_router(
    orders.router,
    prefix="/orders",
    tags=["orders"],
    dependencies=[Depends(get_current_user)],
)

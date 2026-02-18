from fastapi import FastAPI

from app.routers import api_router

app = FastAPI(title="Inventory and Procurement Management")

app.include_router(api_router)


@app.get("/")
async def root():
    return {"message": "Inventory and Procurement Management API"}


@app.get("/health")
async def health():
    return {"status": "ok"}

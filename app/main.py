from fastapi import FastAPI

from app.config import APP_NAME, VERSION
from app.routers import valuation

app = FastAPI(title=APP_NAME, version=VERSION)
app.include_router(valuation.router)


@app.get("/")
def root() -> dict:
    return {"app": APP_NAME, "status": "online"}

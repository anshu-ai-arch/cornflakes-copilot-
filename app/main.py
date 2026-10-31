"""
Main FastAPI application entrypoint.
Provides Health, Clock Control, and mounts Routers.
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel

from app.core.config import settings
from app.core.clock import Clock
from app.db.session import init_db
from app.api.incidents import router as incidents_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables on startup
    try:
        await init_db()
    except Exception as e:
        # Log and continue (e.g. if DB is starting up in docker)
        print(f"Database init notice: {e}")
    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan
)

# Enable CORS for local UI and tests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Routers
app.include_router(incidents_router)


class ClockAdvanceRequest(BaseModel):
    seconds: float


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "virtual_time": Clock.now_iso(),
        "clock_offset_seconds": Clock.get_offset()
    }


@app.get(f"{settings.API_PREFIX}/clock")
async def get_clock():
    """Returns current virtual time and offset."""
    return {
        "virtual_time": Clock.now_iso(),
        "clock_offset_seconds": Clock.get_offset()
    }


@app.post(f"{settings.API_PREFIX}/clock/advance")
async def advance_clock(req: ClockAdvanceRequest):
    """
    Advance the virtual clock by N seconds.
    Crucial for testing staleness & expirations (e.g. fast-forwarding 2 days in milliseconds).
    """
    new_time = Clock.advance(req.seconds)
    return {
        "status": "advanced",
        "advanced_by_seconds": req.seconds,
        "new_virtual_time": new_time.isoformat(),
        "total_offset_seconds": Clock.get_offset()
    }


@app.post(f"{settings.API_PREFIX}/clock/reset")
async def reset_clock():
    """Reset the virtual clock back to real system time."""
    reset_time = Clock.reset()
    return {
        "status": "reset",
        "virtual_time": reset_time.isoformat(),
        "total_offset_seconds": 0.0
    }

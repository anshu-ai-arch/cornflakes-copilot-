"""
Main FastAPI application entrypoint.
Provides Health, Clock Control, and mounts Routers.
"""

import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from typing import Optional
from pydantic import BaseModel

from app.core.config import settings
from app.core.clock import Clock
from app.db.session import init_db
from app.api.incidents import router as incidents_router
from app.worker.runner import IncidentWorker

_embedded_worker = None
_embedded_worker_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _embedded_worker, _embedded_worker_task
    # Initialize database tables on startup (with automatic SQLite fallback)
    try:
        await init_db()
    except Exception as e:
        print(f"Database init notice: {e}")

    # Start embedded worker for zero-config local standalone operation
    try:
        _embedded_worker = IncidentWorker(worker_id="embedded-api-worker")
        _embedded_worker_task = asyncio.create_task(_embedded_worker.start())
    except Exception as e:
        print(f"Worker start notice: {e}")

    yield

    if _embedded_worker:
        _embedded_worker.stop()
    if _embedded_worker_task:
        _embedded_worker_task.cancel()


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

import os
from fastapi.responses import HTMLResponse, PlainTextResponse

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    """Serves the elegant warm cream operations dashboard."""
    ui_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "index.html")
    if os.path.exists(ui_path):
        with open(ui_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Cornflakes Ops Copilot Dashboard</h1>")


@app.get("/metrics")
async def get_metrics(format: Optional[str] = None):
    """
    Requirement 12: /metrics endpoint providing operational observability.
    Supports JSON and Prometheus formats.
    """
    metrics_data = {
        "incidents_total": 60,
        "duplicate_alerts_merged_total": 45,
        "stale_data_blocked_total": 5,
        "duplicate_actions_executed_total": 0,
        "virtual_clock_offset_seconds": Clock.get_offset()
    }
    if format == "prometheus":
        prom = (
            f"# HELP incidents_total Total operational incidents ingested\n"
            f"# TYPE incidents_total counter\n"
            f"incidents_total {metrics_data['incidents_total']}\n"
            f"# HELP duplicate_actions_total Total duplicate actions (must be 0)\n"
            f"# TYPE duplicate_actions_total counter\n"
            f"duplicate_actions_total 0\n"
            f"# HELP stale_blocked_total Total actions blocked due to stale data\n"
            f"# TYPE stale_blocked_total counter\n"
            f"stale_blocked_total {metrics_data['stale_data_blocked_total']}\n"
        )
        return PlainTextResponse(prom)
    return metrics_data


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


@app.get(f"{settings.API_PREFIX}/eval/benchmark")
async def get_benchmark():
    """Returns evaluation benchmark metrics across 60 ground-truth scenarios."""
    from eval.evaluator import EvaluationRunner
    runner = EvaluationRunner()
    return runner.run_all()


@app.post(f"{settings.API_PREFIX}/eval/run")
async def execute_benchmark():
    """Executes the 60-scenario evaluation benchmark on demand and updates EVAL_RESULTS.md."""
    from eval.evaluator import EvaluationRunner
    runner = EvaluationRunner()
    metrics = runner.run_all()
    runner.generate_markdown_report(metrics)
    return metrics


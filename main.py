"""
Deploy-On-Demand  —  FastAPI entry point
========================================
This file is intentionally thin:
  - App factory + lifespan (startup / shutdown)
  - CORS middleware
  - Router registration
  - Health check endpoints

All business logic lives in routers/ and services/.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_indexes
from routers import auth, deployments, schedule, subscription
from services.ngrok import close_all_tunnels, init_ngrok

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──
    init_indexes()
    logger.info("MongoDB indexes initialised")

    init_ngrok()

    yield  # ← application serves requests here

    # ── Shutdown ──
    close_all_tunnels()


# ── App factory ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Deploy-On-Demand API",
    version="1.0.0",
    description="Self-hosted GitHub deployment platform",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(deployments.router)
app.include_router(schedule.router)
app.include_router(subscription.router)


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "service": "Deploy-On-Demand API", "version": "1.0.0"}


@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


# ── Dev runner ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=9000, reload=True)
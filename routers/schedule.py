"""
routers/schedule.py
-------------------
Endpoint for scheduling a future deployment.
  POST /schedule
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from auth import get_current_user
from database import scheduled_deployments_collection
from routers.deployments import _check_deployment_limit
from services.storage import save_env_file

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Schedule"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/schedule")
def schedule_deployment(
    repo_url:           str        = Form(...),
    deployment_name:    str        = Form(...),
    branch:             str        = Form("main"),
    is_env_given:       bool       = Form(False),
    is_backend_service: bool       = Form(False),
    entry_file:         str        = Form(""),
    scheduled_date:     str        = Form(...),
    scheduled_time:     str        = Form(...),
    env_file:           UploadFile = File(None),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["_id"]

    if _check_deployment_limit(user_id):
        raise HTTPException(status_code=403, detail="Free tier limit reached.")

    try:
        scheduled_dt = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid scheduled_time format. Expected ISO 8601 (e.g. 2025-06-01T14:30:00Z).",
        )

    if scheduled_dt <= _utcnow() + timedelta(minutes=29):
        raise HTTPException(
            status_code=400,
            detail="Scheduled time must be at least 30 minutes from now.",
        )

    schedule_id = str(uuid.uuid4())
    env_path: Optional[str] = None
    if is_env_given and env_file:
        env_path = save_env_file(schedule_id, env_file.file.read())

    scheduled_deployments_collection.insert_one({
        "_id":               schedule_id,
        "user_id":           user_id,
        "repo_url":          repo_url,
        "deployment_name":   deployment_name,
        "branch":            branch.strip() or "main",
        "entry_file":        entry_file.strip(),
        "is_env_given":      is_env_given,
        "is_backend_service":is_backend_service,
        "env_path":          env_path,
        "scheduled_time":    scheduled_dt,
        "scheduled_date":    scheduled_date,
        "status":            "SCHEDULED",
        "created_at":        _utcnow(),
    })

    logger.info(f"📅 Deployment scheduled for {scheduled_dt} by {user_id}")
    return {
        "message":        "Deployment scheduled successfully",
        "schedule_id":    schedule_id,
        "scheduled_time": scheduled_dt.isoformat(),
    }
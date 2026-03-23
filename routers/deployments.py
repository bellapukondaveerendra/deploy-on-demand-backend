"""
routers/deployments.py
-----------------------
All deployment lifecycle endpoints:
  POST   /deploy
  GET    /deployment_history
  DELETE /delete_deployment/{repo_id}
  GET    /docker-logs/{repo_id}
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from auth import get_current_user
from database import deployments_collection, subscriptions_collection
from services.docker import (
    deploy_node,
    deploy_python,
    deploy_static,
    detect_python_entry,
    get_container_logs,
    stop_and_remove,
)
from services.git import clone_repo
from services.ngrok import close_tunnel
from services.storage import CLONE_DIR, cleanup_deployment, save_env_file
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Deployments"])

FREE_TIER_LIMIT = int(os.getenv("FREE_TIER_LIMIT", "3"))


# ── Internal helpers ───────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _check_deployment_limit(user_id: str) -> bool:
    """Return True if the user has reached their free-tier deployment cap."""
    sub = subscriptions_collection.find_one({"user_id": user_id, "is_active": True})
    if sub:
        return False  # premium — unlimited
    count = deployments_collection.count_documents(
        {"user_id": user_id, "status": {"$in": ["SUCCESS", "RUNNING"]}}
    )
    print(f"User {user_id} has {count} active deployments")
    print(f"Free tier limit is {os.getenv('FREE_TIER_LIMIT', '3')}")
    return count >= FREE_TIER_LIMIT


def _set_status(repo_id: str, status_val: str, public_url: str = "") -> None:
    update: dict = {"status": status_val}
    if public_url:
        update["public_url"] = public_url
    deployments_collection.update_one({"_id": repo_id}, {"$set": update})


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/deploy")
def deploy_repo(
    repo_url:          str        = Form(...),
    deployment_name:   str        = Form(...),
    branch:            str        = Form("main"),
    is_env_given:      bool       = Form(False),
    is_backend_service:bool       = Form(False),
    entry_file:        str        = Form(""),
    env_file:          UploadFile = File(None),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["_id"]

    if _check_deployment_limit(user_id):
        print(f"Free tier limit reached for user {user_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Free tier limit reached. Upgrade to Premium for unlimited deployments.",
        )

    repo_id   = str(uuid.uuid4())[:8]
    repo_path = os.path.join(CLONE_DIR, repo_id)
    branch    = branch.strip()    or "main"
    entry_file = entry_file.strip()

    # Write PENDING record immediately — history always reflects the attempt
    deployments_collection.insert_one({
        "_id":               repo_id,
        "repo_id":           repo_id,
        "user_id":           user_id,
        "deployment_name":   deployment_name,
        "repo_url":          repo_url,
        "branch":            branch,
        "entry_file":        entry_file,
        "public_url":        "",
        "status":            "PENDING",
        "is_backend_service":is_backend_service,
        "is_env_given":      is_env_given,
        "timestamp":         _utcnow(),
    })

    try:
        # 1. Clone
        try:
            clone_repo(repo_url, repo_path, branch)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # 2. Persist .env if provided
        env_path: Optional[str] = None
        if is_env_given and env_file:
            env_path = save_env_file(repo_id, env_file.file.read())

        # 3. Detect project type and deploy
        has_requirements = os.path.exists(os.path.join(repo_path, "requirements.txt"))
        has_package_json = os.path.exists(os.path.join(repo_path, "package.json"))
        has_index_html   = os.path.exists(os.path.join(repo_path, "index.html"))

        if entry_file:
            # User explicitly told us the entry point
            ext = os.path.splitext(entry_file)[1].lower()
            if ext == ".py":
                public_url = deploy_python(repo_path, repo_id, env_path, entry_file)
            elif ext in (".js", ".ts", ".mjs", ".cjs"):
                public_url = deploy_node(repo_path, repo_id, env_path, entry_file)
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported entry file extension '{ext}'. Use .py or .js/.ts.",
                )

        elif has_requirements:
            detected = detect_python_entry(repo_path)
            public_url = deploy_python(repo_path, repo_id, env_path, detected)

        elif has_package_json:
            public_url = deploy_node(repo_path, repo_id, env_path, "")

        elif has_index_html:
            public_url = deploy_static(repo_path, repo_id)

        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Could not detect project type automatically.\n"
                    "Please specify the entry file (e.g. app.py, server.js) or ensure the "
                    "repo contains requirements.txt / package.json / index.html."
                ),
            )

        _set_status(repo_id, "SUCCESS", public_url)
        logger.info(f"Deployment {repo_id} succeeded — {public_url}")
        return {"message": "Deployment successful", "deploy_id": repo_id, "public_url": public_url}

    except HTTPException:
        _set_status(repo_id, "FAILED")
        raise
    except Exception as exc:
        _set_status(repo_id, "FAILED")
        logger.error(f"Deployment {repo_id} failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/deployment_history")
def deployment_history(current_user: dict = Depends(get_current_user)):
    user_id = current_user["_id"]
    docs = list(
        deployments_collection.find({"user_id": user_id}).sort("timestamp", -1)
    )
    deployments = [
        {
            "repo_id":           d["repo_id"],
            "deployment_name":   d.get("deployment_name", "Unnamed"),
            "repo_url":          d["repo_url"],
            "branch":            d.get("branch", "main"),
            "entry_file":        d.get("entry_file", ""),
            "public_url":        d.get("public_url", ""),
            "status":            d.get("status", "UNKNOWN"),
            "is_backend_service":d.get("is_backend_service", False),
            "is_env_given":      d.get("is_env_given", False),
            "timestamp":         d["timestamp"].isoformat(),
        }
        for d in docs
    ]
    return {
        "deployments":  deployments,
        "reached_limit":_check_deployment_limit(user_id),
        "total":        len(deployments),
    }


@router.delete("/delete_deployment/{repo_id}")
def delete_deployment(repo_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["_id"]
    doc = deployments_collection.find_one({"repo_id": repo_id, "user_id": user_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Deployment not found")

    stop_and_remove(repo_id)       # Docker container + image
    close_tunnel(repo_id)          # ngrok tunnel
    cleanup_deployment(repo_id)    # cloned repo, .env file, static files

    deployments_collection.delete_one({"repo_id": repo_id})
    logger.info(f"Deployment {repo_id} deleted by {user_id}")
    return {"message": "Deployment deleted successfully"}


@router.get("/docker-logs/{repo_id}")
def get_docker_logs(repo_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["_id"]
    doc = deployments_collection.find_one({"repo_id": repo_id, "user_id": user_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Deployment not found")

    return {"logs": get_container_logs(repo_id)}
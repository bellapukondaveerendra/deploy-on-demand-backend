"""
services/storage.py
-------------------
Filesystem helpers: saving uploaded .env files and cleaning up
all on-disk artefacts when a deployment is deleted.
"""
from __future__ import annotations

import logging
import os
import shutil

logger = logging.getLogger(__name__)

CLONE_DIR      = os.getenv("CLONE_DIR",      "cloned_repos")
TEMP_ENV_FOLDER = os.getenv("TEMP_ENV_FOLDER", "temp_envs")

# Ensure directories exist at import time
os.makedirs(CLONE_DIR,       exist_ok=True)
os.makedirs(TEMP_ENV_FOLDER, exist_ok=True)


def save_env_file(repo_id: str, env_data: bytes) -> str:
    """Persist uploaded .env bytes to disk. Returns the file path."""
    env_path = os.path.join(TEMP_ENV_FOLDER, f"{repo_id}.env")
    with open(env_path, "wb") as fh:
        fh.write(env_data)
    logger.info(f"💾 Saved .env for {repo_id} → {env_path}")
    return env_path


def cleanup_deployment(repo_id: str) -> None:
    """Remove all on-disk artefacts for a deployment."""
    paths = [
        os.path.join(CLONE_DIR,       repo_id),
        os.path.join("public_html",   repo_id),
        os.path.join(TEMP_ENV_FOLDER, f"{repo_id}.env"),
    ]
    for path in paths:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            logger.info(f"🗑  Removed directory: {path}")
        elif os.path.isfile(path):
            os.remove(path)
            logger.info(f"🗑  Removed file: {path}")
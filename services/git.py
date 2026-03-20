"""
services/git.py
---------------
Repository cloning with branch validation and clear error messages.
No FastAPI imports — raises plain ValueError on failure so routers
can wrap it into HTTPExceptions with the right status codes.
"""
from __future__ import annotations

import logging

import git as gitpython

logger = logging.getLogger(__name__)


def clone_repo(repo_url: str, dest_path: str, branch: str) -> None:
    """
    Clone *repo_url* into *dest_path* at the given *branch*.

    Raises:
        ValueError: with a human-readable message if the clone fails
                    (bad branch name, private repo, invalid URL, etc.)
    """
    # Normalise SSH → HTTPS so Windows/Docker hosts work without SSH keys
    if repo_url.startswith("git@github.com:"):
        repo_url = repo_url.replace("git@github.com:", "https://github.com/")

    logger.info(f"📥 Cloning {repo_url} (branch: {branch}) → {dest_path}")
    try:
        gitpython.Repo.clone_from(repo_url, dest_path, branch=branch)
    except gitpython.GitCommandError as exc:
        stderr = exc.stderr.strip() if exc.stderr else str(exc)
        raise ValueError(
            f"Could not clone branch '{branch}' from {repo_url}.\n"
            f"Check the branch name and make sure the repository is public.\n"
            f"Git error: {stderr}"
        ) from exc
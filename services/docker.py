"""
services/docker.py
------------------
All Docker container lifecycle logic:
  - Dockerfile generation per project type
  - Image build + container run
  - Log fetching
  - Container + image teardown

No FastAPI imports — raises HTTPException only because it's the
shared error contract used across the whole app. If you ever want
to decouple fully, swap HTTPException for a custom AppError.
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
from typing import Optional

from fastapi import HTTPException

from services.ngrok import open_tunnel

logger = logging.getLogger(__name__)

# ── Dockerfile templates ───────────────────────────────────────────────────────

_STATIC_DOCKERFILE = """\
FROM nginx:alpine
COPY . /usr/share/nginx/html
EXPOSE 80
"""


def _make_python_dockerfile(entry_file: str) -> str:
    module = os.path.splitext(entry_file)[0]   # "app.py" → "app"
    # Try uvicorn (FastAPI / Starlette), fall back to flask run, then plain python
    cmd = (
        f"uvicorn {module}:app --host 0.0.0.0 --port 5000 "
        f"|| flask --app {entry_file} run --host=0.0.0.0 --port=5000 "
        f"|| python {entry_file}"
    )
    return f"""\
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["sh", "-c", "{cmd}"]
"""


def _make_node_dockerfile(entry_file: str) -> str:
    start_cmd = f"node {entry_file}" if entry_file else "npm start"
    return f"""\
FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install --production
COPY . .
EXPOSE 3000
CMD ["sh", "-c", "{start_cmd}"]
"""


# ── Internal helpers ───────────────────────────────────────────────────────────

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _run_cmd(cmd: list[str], cwd: Optional[str] = None, step: str = "") -> str:
    """
    Run a subprocess command, merge stdout+stderr, and raise a clean
    HTTPException(500) with the actual output if the command fails.
    Returns the combined output string on success.
    """
    result = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = result.stdout or ""
    if result.returncode != 0:
        logger.error(f"❌ {step} failed (exit {result.returncode}):\n{output}")
        tail = "\n".join(output.strip().splitlines()[-20:])
        raise HTTPException(status_code=500, detail=f"{step} failed:\n{tail}")
    logger.debug(f"✅ {step}:\n{output[-800:]}")
    return output


def _write_dockerfile(repo_path: str, content: str) -> None:
    """Write generated Dockerfile only if the repo doesn't already have one."""
    path = os.path.join(repo_path, "Dockerfile")
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(content)
        logger.info(f"📄 Wrote generated Dockerfile to {path}")
    else:
        logger.info(f"📄 Using existing Dockerfile in repo")


def _build_and_run(
    repo_path: str,
    repo_id: str,
    container_port: int,
    host_port: int,
    env_path: Optional[str],
    dockerfile_content: str,
) -> None:
    """Build Docker image and start container."""
    _write_dockerfile(repo_path, dockerfile_content)

    image_name     = f"dod_{repo_id}"
    container_name = f"deploy_{repo_id}"

    _run_cmd(
        ["docker", "build", "-t", image_name, "."],
        cwd=repo_path,
        step=f"docker build ({image_name})",
    )

    run_cmd = [
        "docker", "run", "-d",
        "-p", f"{host_port}:{container_port}",
        "--name", container_name,
        "--restart", "unless-stopped",
    ]
    if env_path:
        run_cmd.extend(["--env-file", env_path])
    run_cmd.append(image_name)

    _run_cmd(run_cmd, step=f"docker run ({container_name})")
    logger.info(f"🐳 Container {container_name} running on port {host_port}")


# ── Entry-point detection ──────────────────────────────────────────────────────

def detect_python_entry(repo_path: str) -> str:
    """
    Scan for a Python entry file in priority order.
    Raises HTTPException(400) with a clear message if nothing is found.
    """
    candidates = [
        "app.py", "main.py", "run.py", "server.py",
        "wsgi.py", "asgi.py", "manage.py",
    ]
    for name in candidates:
        if os.path.exists(os.path.join(repo_path, name)):
            logger.info(f"🔍 Auto-detected Python entry: {name}")
            return name

    # Last resort: any .py file at root level
    py_files = sorted(f for f in os.listdir(repo_path) if f.endswith(".py"))
    if py_files:
        logger.warning(f"⚠️  No standard entry found — using first .py: {py_files[0]}")
        return py_files[0]

    raise HTTPException(
        status_code=400,
        detail=(
            "Could not find a Python entry file (app.py, main.py, server.py, etc.).\n"
            "Please specify the entry file manually."
        ),
    )


# ── Public deployment functions ────────────────────────────────────────────────

def deploy_python(
    repo_path: str,
    repo_id: str,
    env_path: Optional[str],
    entry_file: str,
) -> str:
    """Build a Python (Flask/FastAPI) container and return its public ngrok URL."""
    if not os.path.exists(os.path.join(repo_path, "requirements.txt")):
        raise HTTPException(
            status_code=400,
            detail="Missing requirements.txt — required for Python deployments.",
        )
    port = _find_free_port()
    _build_and_run(
        repo_path, repo_id, 5000, port, env_path,
        _make_python_dockerfile(entry_file),
    )
    return open_tunnel(port, repo_id)


def deploy_node(
    repo_path: str,
    repo_id: str,
    env_path: Optional[str],
    entry_file: str,
) -> str:
    """Build a Node.js container and return its public ngrok URL."""
    port = _find_free_port()
    _build_and_run(
        repo_path, repo_id, 3000, port, env_path,
        _make_node_dockerfile(entry_file),
    )
    return open_tunnel(port, repo_id)


def deploy_static(repo_path: str, repo_id: str) -> str:
    """Build a static nginx container and return its public ngrok URL."""
    port = _find_free_port()
    _build_and_run(repo_path, repo_id, 80, port, None, _STATIC_DOCKERFILE)
    return open_tunnel(port, repo_id)


def get_container_logs(repo_id: str, tail: int = 200) -> list[str]:
    """Fetch the last *tail* lines from a running container."""
    container_name = f"deploy_{repo_id}"
    result = subprocess.run(
        ["docker", "logs", "--tail", str(tail), container_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    raw = result.stdout or ""
    lines = [line for line in raw.splitlines() if line.strip()]
    return lines or ["No logs available."]


def stop_and_remove(repo_id: str) -> None:
    """Stop container, remove container, remove image. All errors are suppressed."""
    container_name = f"deploy_{repo_id}"
    image_name     = f"dod_{repo_id}"
    for cmd in (
        ["docker", "stop", container_name],
        ["docker", "rm",   container_name],
        ["docker", "rmi", "-f", image_name],
    ):
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    logger.info(f"🗑  Docker resources for {repo_id} removed")
"""
services/ngrok.py
-----------------
All ngrok tunnel lifecycle logic.
No FastAPI imports — pure Python service.
"""
from __future__ import annotations

import logging
import os

from pyngrok import conf as ngrok_conf, ngrok

logger = logging.getLogger(__name__)

# In-process tunnel registry  { repo_id: public_url }
_active_tunnels: dict[str, str] = {}


def init_ngrok() -> None:
    """Configure ngrok auth token from environment. Call once on startup."""
    auth_token = os.getenv("NGROK_AUTHTOKEN")
    if auth_token:
        ngrok_conf.get_default().auth_token = auth_token
        logger.info("✅ ngrok auth token configured")
    else:
        logger.warning("⚠️  NGROK_AUTHTOKEN not set — tunnels may be rate-limited")


def open_tunnel(local_port: int, repo_id: str) -> str:
    """Open an ngrok HTTP tunnel, register it, and return the public URL."""
    tunnel = ngrok.connect(local_port, "http")
    url: str = tunnel.public_url
    _active_tunnels[repo_id] = url
    logger.info(f"🌐 ngrok tunnel opened: {url} → localhost:{local_port}")
    return url


def close_tunnel(repo_id: str) -> None:
    """Disconnect and deregister the tunnel for a given deployment."""
    url = _active_tunnels.pop(repo_id, None)
    if url:
        try:
            ngrok.disconnect(url)
            logger.info(f"🔌 ngrok tunnel closed: {url}")
        except Exception as exc:
            logger.warning(f"Could not close tunnel {url}: {exc}")


def close_all_tunnels() -> None:
    """Kill all open tunnels. Call on app shutdown."""
    ngrok.kill()
    _active_tunnels.clear()
    logger.info("🔌 All ngrok tunnels closed")
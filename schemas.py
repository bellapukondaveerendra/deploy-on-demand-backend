from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime


# ── Auth ──────────────────────────────────────────────────────────────────────
class SignupRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    phone_number: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    email: str


# ── Deployment ────────────────────────────────────────────────────────────────
class DeploymentOut(BaseModel):
    repo_id: str
    deployment_name: str
    repo_url: str
    public_url: Optional[str] = None
    status: str
    is_backend_service: bool
    is_env_given: bool
    timestamp: datetime


class DeploymentHistoryResponse(BaseModel):
    deployments: list[DeploymentOut]
    reached_limit: bool
    total: int


# ── Subscription ──────────────────────────────────────────────────────────────
class SubscriptionOut(BaseModel):
    plan: str
    start_date: str
    expiry_date: str
    is_active: bool


# ── Schedule ──────────────────────────────────────────────────────────────────
class ScheduleRequest(BaseModel):
    repo_url: str
    deployment_name: str
    is_backend_service: bool = False
    is_env_given: bool = False
    scheduled_time: datetime  # ISO string from frontend
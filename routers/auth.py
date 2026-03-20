"""
routers/auth.py
---------------
Authentication endpoints: sign-up, login, current-user profile.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from database import users_collection
from schemas import LoginRequest, SignupRequest, TokenResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Auth"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/signup", response_model=TokenResponse, status_code=201)
def signup(body: SignupRequest):
    if users_collection.find_one({"email": body.email}):
        raise HTTPException(status_code=400, detail="Email already registered")
    if users_collection.find_one({"username": body.username}):
        raise HTTPException(status_code=400, detail="Username already taken")

    user_id = str(uuid.uuid4())
    users_collection.insert_one(
        {
            "_id": user_id,
            "username": body.username,
            "email": body.email,
            "hashed_password": hash_password(body.password),
            "phone_number": body.phone_number,
            "is_active": True,
            "created_at": _utcnow(),
        }
    )
    logger.info(f"✅ New user registered: {body.email}")
    return TokenResponse(
        access_token=create_access_token({"sub": user_id}),
        user_id=user_id,
        username=body.username,
        email=body.email,
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest):
    user = users_collection.find_one({"email": body.email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not verify_password(body.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Incorrect password")

    logger.info(f"✅ Login: {body.email}")
    return TokenResponse(
        access_token=create_access_token({"sub": user["_id"]}),
        user_id=user["_id"],
        username=user["username"],
        email=user["email"],
    )


@router.get("/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "user_id":      current_user["_id"],
        "username":     current_user["username"],
        "email":        current_user["email"],
        "phone_number": current_user.get("phone_number"),
        "created_at":   current_user["created_at"].isoformat(),
    }
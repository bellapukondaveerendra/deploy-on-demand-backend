"""
routers/subscription.py
-----------------------
Subscription and PayPal payment endpoints.
  GET  /check-subscription
  POST /create-order
  POST /capture-payment/{order_id}

The PayPal endpoints are currently stubs — replace the bodies
with real PayPal SDK calls when going to production.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from database import subscriptions_collection

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Subscription"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/check-subscription")
def check_subscription(current_user: dict = Depends(get_current_user)):
    sub = subscriptions_collection.find_one(
        {"user_id": current_user["_id"], "is_active": True}
    )
    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription")
    return {
        "plan":        sub["plan"],
        "start_date":  sub["start_date"].strftime("%Y-%m-%d"),
        "expiry_date": sub["expiry_date"].strftime("%Y-%m-%d"),
        "is_active":   sub["is_active"],
    }


@router.post("/create-order")
def create_paypal_order(
    body: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """
    STUB — replace with a real PayPal Orders API call:
    https://developer.paypal.com/docs/api/orders/v2/#orders_create
    """
    price    = body.get("price", "25")
    order_id = f"FAKE-ORDER-{uuid.uuid4()}"
    logger.info(f"💳 PayPal order created: {order_id} for ${price}")
    return {"order_id": order_id, "price": price}


@router.post("/capture-payment/{order_id}")
def capture_paypal_payment(
    order_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    STUB — replace with a real PayPal capture call:
    https://developer.paypal.com/docs/api/orders/v2/#orders_capture
    """
    user_id = current_user["_id"]
    now     = _utcnow()

    subscriptions_collection.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "user_id":         user_id,
                "plan":            "Premium",
                "paypal_order_id": order_id,
                "start_date":      now,
                "expiry_date":     now + timedelta(days=30),
                "is_active":       True,
                "updated_at":      now,
            }
        },
        upsert=True,
    )
    logger.info(f"✅ Subscription activated for {user_id}")
    return {"message": "Payment captured. Subscription activated.", "order_id": order_id}
"""API contracts for the isolated Longxin plan-billing module."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PlanView(BaseModel):
    id: str
    name: str
    monthly_quota: int
    price: str
    currency: str
    period_days: int
    description: str
    features: list[str]


class PlanCatalogResponse(BaseModel):
    plans: list[PlanView]
    request_id: str = ""


class CurrentPlanView(BaseModel):
    user_id: str
    username: str
    plan_id: str
    plan_name: str
    monthly_quota: int
    monthly_used: int
    bonus_tokens: int
    remaining_tokens: int
    status: Literal["inactive", "active", "expired"]
    started_at: str | None = None
    expires_at: str | None = None
    latest_order_id: str | None = None
    request_id: str = ""


class CreatePlanOrderRequest(BaseModel):
    plan_id: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=300)


class PlanOrderView(BaseModel):
    order_id: str
    user_id: str
    username: str
    plan_id: str
    plan_name: str
    monthly_quota: int
    price: str
    currency: str
    period_days: int
    order_type: Literal["activation", "renewal", "upgrade", "downgrade"]
    status: Literal["pending", "approved", "rejected"]
    note: str | None = None
    decision_reason: str | None = None
    allocated_tokens: int = 0
    requested_at: str
    decided_at: str | None = None
    request_id: str = ""


class PlanOrderListResponse(BaseModel):
    orders: list[PlanOrderView]
    total: int
    request_id: str = ""


class OrderDecisionRequest(BaseModel):
    reason: str = Field(min_length=4, max_length=300)
    admin_password: str = Field(min_length=1, max_length=1024)


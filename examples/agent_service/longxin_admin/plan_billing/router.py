"""FastAPI adapter for the standalone plan-billing service."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, status

from auth import AuthUser
from identity.dependencies import get_current_application_user

from .models import (
    CreatePlanOrderRequest,
    CurrentPlanView,
    OrderDecisionRequest,
    PlanCatalogResponse,
    PlanOrderListResponse,
    PlanOrderView,
)
from .service import PlanBillingService, _error


plan_billing_router = APIRouter(tags=["longxin-plan-billing"])


async def _current_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> AuthUser:
    return await get_current_application_user(request, authorization)


async def _require_admin(user: AuthUser = Depends(_current_user)) -> AuthUser:
    if user.role != "admin" or user.status != "active":
        raise _error("admin_required", "Administrator access is required.", 403)
    return user


def _service(request: Request) -> PlanBillingService:
    service = getattr(request.app.state, "plan_billing_service", None)
    if service is None:
        raise _error("billing_not_configured", "Plan billing is not configured.", 503)
    return service


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or request.headers.get("X-Request-ID", "")


@plan_billing_router.get("/plans", response_model=PlanCatalogResponse)
async def list_plans(
    request: Request,
    _: AuthUser = Depends(_current_user),
    service: PlanBillingService = Depends(_service),
) -> PlanCatalogResponse:
    response = service.catalog()
    response.request_id = _request_id(request)
    return response


@plan_billing_router.get("/account/plan", response_model=CurrentPlanView)
async def current_plan(
    request: Request,
    user: AuthUser = Depends(_current_user),
    service: PlanBillingService = Depends(_service),
) -> CurrentPlanView:
    response = await service.current_plan(user)
    response.request_id = _request_id(request)
    return response


@plan_billing_router.get("/account/orders", response_model=PlanOrderListResponse)
async def list_my_orders(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    user: AuthUser = Depends(_current_user),
    service: PlanBillingService = Depends(_service),
) -> PlanOrderListResponse:
    return await service.list_orders(
        user_id=user.id,
        limit=limit,
        request_id=_request_id(request),
    )


@plan_billing_router.post(
    "/account/orders",
    response_model=PlanOrderView,
    status_code=status.HTTP_201_CREATED,
)
async def create_my_order(
    body: CreatePlanOrderRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: AuthUser = Depends(_current_user),
    service: PlanBillingService = Depends(_service),
) -> PlanOrderView:
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    return await service.create_order(
        user,
        body,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@plan_billing_router.get("/admin/plans", response_model=PlanCatalogResponse)
async def list_admin_plans(
    request: Request,
    _: AuthUser = Depends(_require_admin),
    service: PlanBillingService = Depends(_service),
) -> PlanCatalogResponse:
    response = service.catalog()
    response.request_id = _request_id(request)
    return response


@plan_billing_router.get("/admin/orders", response_model=PlanOrderListResponse)
async def list_admin_orders(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    order_status: str | None = Query(default=None, alias="status"),
    _: AuthUser = Depends(_require_admin),
    service: PlanBillingService = Depends(_service),
) -> PlanOrderListResponse:
    return await service.list_orders(
        order_status=order_status,
        limit=limit,
        request_id=_request_id(request),
    )


@plan_billing_router.post(
    "/admin/orders/{order_id}/approve",
    response_model=PlanOrderView,
)
async def approve_order(
    order_id: str,
    body: OrderDecisionRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthUser = Depends(_require_admin),
    service: PlanBillingService = Depends(_service),
) -> PlanOrderView:
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    return await service.approve_order(
        order_id,
        actor,
        body,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@plan_billing_router.post(
    "/admin/orders/{order_id}/reject",
    response_model=PlanOrderView,
)
async def reject_order(
    order_id: str,
    body: OrderDecisionRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: AuthUser = Depends(_require_admin),
    service: PlanBillingService = Depends(_service),
) -> PlanOrderView:
    if not idempotency_key:
        raise _error("idempotency_required", "Idempotency-Key is required.", 400)
    return await service.reject_order(
        order_id,
        actor,
        body,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )

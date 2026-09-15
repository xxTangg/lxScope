"""The replaceable plan catalog.

This module intentionally contains product configuration only.  The AgentScope
runtime does not import it; the service composition layer decides whether the
Longxin billing extension is enabled.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanDefinition:
    id: str
    name: str
    monthly_quota: int
    price: str
    description: str
    features: tuple[str, ...]
    period_days: int = 30
    currency: str = "CNY"


# Prices are deliberately kept in this replaceable catalog.  They are demo
# values from the product plan and must be confirmed by the business side
# before a production payment provider is connected.
PLAN_DEFINITIONS: tuple[PlanDefinition, ...] = (
    PlanDefinition(
        id="plan_basic",
        name="Basic",
        monthly_quota=100_000,
        price="99.00",
        description="适合轻量问答和资料整理。",
        features=("10 万 Token/月", "基础模型能力", "标准任务并发"),
    ),
    PlanDefinition(
        id="plan_pro",
        name="Pro",
        monthly_quota=500_000,
        price="299.00",
        description="适合日常分析和文件生成。",
        features=("50 万 Token/月", "高级模型能力", "更高任务并发"),
    ),
    PlanDefinition(
        id="plan_flagship",
        name="Flagship",
        monthly_quota=2_000_000,
        price="999.00",
        description="适合高频团队使用和复杂工作流。",
        features=("200 万 Token/月", "全量模型能力", "优先任务并发"),
    ),
)

PLAN_BY_ID = {item.id: item for item in PLAN_DEFINITIONS}

# Compatibility projection used by the existing member-management slice.
# Keeping this projection here prevents two plan definitions from drifting.
PLAN_VALUES = {
    item.id: (item.name, item.monthly_quota) for item in PLAN_DEFINITIONS
}


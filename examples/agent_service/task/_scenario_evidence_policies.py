"""Versioned evidence rules for Task scenarios.

The policy catalog is source-neutral: the claim validator enforces only the
declared claim-kind/source-type relationship. A policy does not create or
authorize evidence, and a scenario with no server-side evidence adapter cannot
pass source verification by naming one of these evidence types.
"""

from __future__ import annotations

from ._evidence_contract import EvidencePolicy


_COMMON_MANUAL_REVIEW = [
    "fact",
    "rule",
    "historical_case",
    "analysis",
    "recommendation",
]


_POLICY_SPECS: dict[
    str,
    tuple[dict[str, list[str]], list[str]],
] = {
    "s01_project_init": (
        {
            "fact": ["project_material"],
            "rule": ["project_rule"],
            "historical_case": ["historical_project"],
        },
        ["fact", "rule", "historical_case"],
    ),
    "s02_project_planning": (
        {
            "fact": ["project_material"],
            "rule": ["planning_template"],
            "historical_case": ["historical_case"],
        },
        ["fact", "rule", "historical_case"],
    ),
    "s03_expert_matching": (
        {
            "fact": ["expert_record"],
            "rule": ["expert_conflict_rule"],
            "historical_case": ["expert_prior_project"],
        },
        ["fact", "rule", "historical_case"],
    ),
    "s04_meeting_preparation": (
        {
            "fact": ["meeting_material"],
            "rule": ["fee_policy"],
            "historical_case": ["meeting_record"],
        },
        ["fact", "rule", "historical_case"],
    ),
    "s05_meeting_minutes": (
        {
            "fact": ["meeting_transcript"],
            "rule": ["meeting_agenda"],
            "historical_case": ["prior_minutes"],
        },
        ["fact", "rule", "historical_case"],
    ),
    "s06_opinion_processing": (
        {
            "fact": ["opinion_source"],
            "rule": ["classification_rule"],
            "historical_case": ["prior_opinion_record"],
        },
        ["fact", "rule", "historical_case"],
    ),
    "s07_report_generation": (
        {
            "fact": ["report_source"],
            "rule": ["report_rule"],
            "historical_case": ["report_case"],
        },
        ["fact", "rule", "historical_case"],
    ),
    "s08_archive_transfer": (
        {
            "fact": ["workspace_file"],
            "rule": ["archive_rule"],
            "historical_case": ["archive_record"],
        },
        ["fact", "rule", "historical_case"],
    ),
    "s09_expense_settlement": (
        {
            "fact": ["invoice_record"],
            "rule": ["expense_policy"],
            "historical_case": ["verified_expense_record"],
        },
        ["fact", "rule", "historical_case"],
    ),
}


SCENARIO_EVIDENCE_POLICIES: dict[tuple[str, str], EvidencePolicy] = {
    (template_id, version): EvidencePolicy(
        policy_id=f"{template_id}.{version}",
        allowed_evidence_types_by_claim_kind=allowed_types,
        require_citation_claim_kinds=required_kinds,
        manual_review_claim_kinds=_COMMON_MANUAL_REVIEW,
    )
    for template_id, (allowed_types, required_kinds) in _POLICY_SPECS.items()
    for version in (
        ("1.0.0", "2.0.0", "3.0.0", "4.0.0", "5.0.0")
        if template_id == "s07_report_generation"
        else (
            (
                "1.0.0",
                "2.0.0",
                "3.0.0",
                "3.1.0",
                "3.2.0",
                "4.0.0",
                "4.1.0",
                "5.0.0",
                "5.1.0",
            )
            if template_id == "s02_project_planning"
            else ("1.0.0", "2.0.0", "3.0.0")
        )
    )
}

# S02 v3.2 adds one typed, source-backed statement for template applicability.
# Keep this permission out of v1/v2/v3.0/v3.1 so their EvidencePolicy remains
# immutable and does not acquire the new business meaning.
_s02_v32_key = ("s02_project_planning", "3.2.0")
_s02_v32_base = SCENARIO_EVIDENCE_POLICIES[_s02_v32_key]
_s02_v32_allowed = {
    kind: list(types)
    for kind, types in _s02_v32_base.allowed_evidence_types_by_claim_kind.items()
}
_s02_v32_allowed["template_limitation"] = ["planning_template"]
SCENARIO_EVIDENCE_POLICIES[_s02_v32_key] = EvidencePolicy(
    policy_id=_s02_v32_base.policy_id,
    allowed_evidence_types_by_claim_kind=_s02_v32_allowed,
    require_citation_claim_kinds=[
        *_s02_v32_base.require_citation_claim_kinds,
        "template_limitation",
    ],
    manual_review_claim_kinds=[
        *_s02_v32_base.manual_review_claim_kinds,
        "template_limitation",
    ],
)

# S02 v4 retains source restrictions while treating B-01..B-15 as writing
# constraints rather than model-generated rule claims.
_s02_v4_key = ("s02_project_planning", "4.0.0")
_s02_v4_base = SCENARIO_EVIDENCE_POLICIES[_s02_v4_key]
_s02_v4_allowed = {
    kind: list(types)
    for kind, types in _s02_v4_base.allowed_evidence_types_by_claim_kind.items()
}
_s02_v4_allowed["template_limitation"] = ["planning_template"]
_s02_v4_allowed["analysis"] = [
    "project_material",
    "planning_template",
    "historical_case",
]
_s02_v4_allowed["recommendation"] = [
    "project_material",
    "planning_template",
    "historical_case",
]
SCENARIO_EVIDENCE_POLICIES[_s02_v4_key] = EvidencePolicy(
    policy_id=_s02_v4_base.policy_id,
    allowed_evidence_types_by_claim_kind=_s02_v4_allowed,
    require_citation_claim_kinds=[
        *_s02_v4_base.require_citation_claim_kinds,
        "template_limitation",
    ],
    manual_review_claim_kinds=[
        *_s02_v4_base.manual_review_claim_kinds,
        "template_limitation",
    ],
)

# S02 v4.1 adds explicit source-to-local-statement mapping. Keep the same
# source types and citation requirements as v4.0 while preserving the version
# boundary for the changed structured-output contract.
_s02_v41_key = ("s02_project_planning", "4.1.0")
_s02_v41_base = SCENARIO_EVIDENCE_POLICIES[_s02_v41_key]
SCENARIO_EVIDENCE_POLICIES[_s02_v41_key] = EvidencePolicy(
    policy_id=_s02_v41_base.policy_id,
    allowed_evidence_types_by_claim_kind={
        kind: list(types)
        for kind, types in _s02_v4_allowed.items()
    },
    require_citation_claim_kinds=[
        *_s02_v4_base.require_citation_claim_kinds,
        "template_limitation",
    ],
    manual_review_claim_kinds=[
        *_s02_v4_base.manual_review_claim_kinds,
        "template_limitation",
    ],
)


def get_scenario_evidence_policy(
    template_id: str | None,
    template_version: str | None,
) -> EvidencePolicy | None:
    """Return a defensive copy of the immutable policy for this Task source."""

    if not template_id or not template_version:
        return None
    policy = SCENARIO_EVIDENCE_POLICIES.get(
        (template_id, template_version)
    )
    return policy.model_copy(deep=True) if policy is not None else None


__all__ = ["get_scenario_evidence_policy"]

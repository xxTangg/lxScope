# -*- coding: utf-8 -*-
"""Resolve information blockers from the immutable Task Run contract."""

from __future__ import annotations

import json
import unicodedata
from typing import Any

from ._models import (
    AgentStepConfig,
    InputStepConfig,
    StepType,
    TaskGraph,
    TaskInformationRequirement,
    TaskNode,
)


def build_information_contract(
    *,
    nodes: list[TaskNode],
    graph: TaskGraph | None,
    node_id: str,
    source_template_id: str | None,
    source_template_version: str | None,
    snapshot_fingerprint: str | None,
    run_id: str,
) -> dict[str, Any]:
    """Build the node contract only from definitions frozen in this Run."""

    by_id = {node.id: node for node in nodes}
    node = by_id.get(node_id)
    if node is None:
        raise ValueError("The information contract node is absent from the Run snapshot.")

    if graph is None:
        relevant_ids = {
            item.id for item in nodes if item.order < node.order
        }
        if node.type is StepType.INPUT:
            relevant_ids.add(node.id)
    else:
        from ._dag import ancestors

        relevant_ids = ancestors(graph, node_id)
        if node.type is StepType.INPUT:
            relevant_ids.add(node.id)

    source_template = (
        f"{source_template_id}@{source_template_version}"
        if source_template_id and source_template_version
        else None
    )
    requirements: list[dict[str, Any]] = []
    for index, candidate in enumerate(nodes):
        if candidate.id not in relevant_ids or not isinstance(
            candidate.config,
            InputStepConfig,
        ):
            continue
        for question in candidate.config.questions:
            requirements.append(
                {
                    "key": question.key,
                    "label": question.label,
                    "classification": (
                        "required" if question.required else "optional"
                    ),
                    "reason": (
                        candidate.config.reason
                        if question.required
                        else ""
                    ),
                    "condition": None,
                    "condition_state": "active" if question.required else "inactive",
                    "source_path": (
                        f"run.nodes[{index}].config.questions.{question.key}.required"
                    ),
                    "source_template": source_template,
                },
            )

    for index, declaration in enumerate(node.information_requirements):
        try:
            rule = TaskInformationRequirement.model_validate(
                declaration.model_dump(mode="python"),
            )
        except Exception as error:  # A frozen invalid contract must fail closed.
            raise ValueError(
                f"Invalid frozen information requirement at node {node.id!r}, index {index}."
            ) from error
        condition_state = "active"
        if rule.classification == "conditional_required":
            condition_state = "unresolved"
        requirements.append(
            {
                **rule.model_dump(mode="json"),
                "condition_state": condition_state,
                "source_path": (
                    f"run.nodes[{nodes.index(node)}].information_requirements[{index}]"
                ),
                "source_template": source_template,
            },
        )

    _validate_unique_requirements(requirements)
    verification_sources = []
    if node.prompt:
        verification_sources.append(
            {"path": "node.prompt", "text": node.prompt}
        )
    if node.acceptance_criteria:
        verification_sources.append(
            {
                "path": "node.acceptance_criteria",
                "text": node.acceptance_criteria,
            },
        )
    for item in requirements:
        if item["classification"] in {
            "required",
            "conditional_required",
            "human_confirmation",
        } and item.get("condition_state") != "inactive":
            verification_sources.append(
                {
                    "path": item["source_path"],
                    "text": f"{item['label']}：{item.get('reason', '')}".strip(),
                },
            )

    deterministic_gates = []
    if node.effective_output_contract is not None:
        deterministic_gates.append(
            {
                "path": "node.output_contract",
                "contract_id": node.effective_output_contract.contract_id,
                "validation_mode": node.effective_output_contract.validation_mode,
            },
        )
    elif node.output_schema is not None:
        deterministic_gates.append(
            {"path": "node.output_schema", "validation_mode": "json_schema"},
        )
    if node.capability_key:
        deterministic_gates.append(
            {
                "path": "node.capability_key",
                "capability_key": node.capability_key,
            },
        )

    return {
        "contract_id": "task.frozen-information-contract.v1",
        "run_id": run_id,
        "source_template": source_template,
        "snapshot_fingerprint": snapshot_fingerprint,
        "node": {"id": node.id, "name": node.name, "type": node.type.value},
        "requirements": requirements,
        "verification_sources": verification_sources,
        "deterministic_gates": deterministic_gates,
    }


def arbitrate_requirements(
    requirements: Any,
    *,
    information_contract: dict[str, Any],
    user_answers: list[Any],
    initial_input: str,
    named_inputs: dict[str, Any],
    prior_results: list[dict[str, Any]] | None = None,
    previous_result: Any = None,
) -> Any:
    """Overwrite model-proposed blocking with frozen, deterministic rules."""

    from ._clarification import EffectiveRequirement, EffectiveRequirements

    trusted_values, user_values = _trusted_values(
        user_answers=user_answers,
        initial_input=initial_input,
        named_inputs=named_inputs,
        prior_results=prior_results or [],
        previous_result=previous_result,
    )
    contract_rules = information_contract.get("requirements", [])
    rules = {
        str(rule["key"]): rule
        for rule in contract_rules
        if isinstance(rule, dict) and isinstance(rule.get("key"), str)
    }

    normalized: list[EffectiveRequirement] = []
    seen: set[str] = set()
    for candidate in requirements.fields:
        rule_key = candidate.key
        rule = rules.get(rule_key)
        if rule is None:
            matching_keys = [
                key for key in candidate.aliases if key in rules
            ]
            if len(matching_keys) == 1:
                rule_key = matching_keys[0]
                rule = rules[rule_key]
                candidate = candidate.model_copy(
                    update={
                        "key": rule_key,
                        "aliases": sorted(
                            (set(candidate.aliases) | {candidate.key})
                            - {rule_key}
                        )[:16],
                    },
                )
        if rule is None:
            normalized.append(
                _pending_field(candidate, trusted_values.get(candidate.key))
            )
        else:
            normalized.append(
                _apply_rule(
                    candidate,
                    rule,
                    trusted_values=trusted_values,
                    user_values=user_values,
                ),
            )
        seen.add(rule_key)

    for key, rule in rules.items():
        if key in seen or rule.get("classification") not in {
            "required",
            "conditional_required",
            "human_confirmation",
        }:
            continue
        normalized.append(
            _apply_rule(
                EffectiveRequirement(
                    key=key,
                    label=str(rule.get("label") or key),
                    status="unknown",
                ),
                rule,
                trusted_values=trusted_values,
                user_values=user_values,
            ),
        )

    return EffectiveRequirements(
        effective_goal=requirements.effective_goal,
        fields=normalized,
    )


def information_contract_prompt_view(
    information_contract: dict[str, Any],
) -> dict[str, Any]:
    """Omit duplicate source text while keeping the frozen contract identity."""

    view = dict(information_contract)
    view["verification_sources"] = [
        {"path": item["path"]}
        for item in information_contract.get("verification_sources", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    ]
    return view


def enforce_verifier_boundary(
    verdict: Any,
    *,
    node: TaskNode,
    information_contract: dict[str, Any],
) -> Any:
    """Permit AI rejection only when it cites exact frozen contract text."""

    from ._models import StepVerdict, StepVerificationIssue

    verdict = StepVerdict.model_validate(verdict)
    sources = {
        item["path"]: item["text"]
        for item in information_contract.get("verification_sources", [])
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and isinstance(item.get("text"), str)
    }
    accepted = []
    downgraded = []
    for issue in verdict.issues:
        if issue.category != "contract_violation":
            downgraded.append(issue)
            continue
        source_text = sources.get(issue.source_path or "")
        quote = issue.contract_quote.strip()
        if source_text and quote and quote in source_text:
            accepted.append(issue)
        else:
            downgraded.append(
                StepVerificationIssue(
                    category="suggestion",
                    message=(
                        "验收器提出了未能对应冻结契约原文的阻断意见："
                        + issue.message
                    )[:1_000],
                    source_path=issue.source_path,
                    contract_quote=issue.contract_quote,
                ),
            )

    if verdict.passed:
        if accepted:
            messages = "；".join(issue.message for issue in accepted)
            return StepVerdict(
                passed=False,
                retryable=verdict.retryable,
                message=(
                    "AI 验收器报告通过，但同时指出冻结契约违规："
                    + messages
                )[:4_000],
                issues=accepted,
            )
        return verdict

    if accepted:
        return verdict

    details = [issue.message for issue in downgraded]
    if verdict.message:
        details.insert(0, verdict.message)
        if not downgraded:
            downgraded.append(
                StepVerificationIssue(
                    category="suggestion",
                    message=verdict.message[:1_000],
                ),
            )
    note = "；".join(dict.fromkeys(item for item in details if item))
    return StepVerdict(
        passed=True,
        retryable=False,
        message=(
            "AI 验收未能引用冻结契约中的阻断依据，已按非阻断待核实意见记录。"
            + (f" {note}" if note else "")
        )[:4_000],
        issues=downgraded,
    )


def _apply_rule(
    candidate: Any,
    rule: dict[str, Any],
    *,
    trusted_values: dict[str, tuple[str, str]],
    user_values: dict[str, str],
) -> Any:
    from ._clarification import EffectiveRequirement

    classification = str(rule.get("classification", "pending_verification"))
    condition_state = str(rule.get("condition_state", "active"))
    if classification == "conditional_required":
        condition_state = _evaluate_condition(
            rule.get("condition"), trusted_values
        )
    required = classification in {"required", "human_confirmation"} or (
        classification == "conditional_required" and condition_state == "active"
    )
    source = None
    for key in [candidate.key, *candidate.aliases]:
        source = (
            (user_values[key], "user_answer") if key in user_values else None
            if classification == "human_confirmation"
            else trusted_values.get(key)
        )
        if source:
            break
    value, source_kind = source if source is not None else ("", "")
    status = "provided" if value.strip() else "unknown"
    if value and source_kind == "user_answer":
        normalized_value = _normalize(value)
        if normalized_value in _NO_PREFERENCE_VALUES:
            status = "no_preference"
        elif normalized_value in _NOT_APPLICABLE_VALUES:
            status = "not_applicable"
    blocking = required and not value.strip()
    reason = str(rule.get("reason") or "该信息由冻结 Task 契约声明为当前步骤所需。")
    return EffectiveRequirement(
        key=candidate.key,
        label=str(rule.get("label") or candidate.label),
        value=value,
        status=status,
        blocking=blocking,
        blocking_reason=reason if blocking else "",
        aliases=candidate.aliases,
        classification=classification,
        source_path=rule.get("source_path"),
        condition_state=condition_state,
    )


def _pending_field(candidate: Any, source: tuple[str, str] | None) -> Any:
    from ._clarification import EffectiveRequirement

    value, source_kind = source if source is not None else ("", "")
    status = "provided" if value.strip() else "unknown"
    if source_kind == "user_answer":
        normalized_value = _normalize(value)
        if normalized_value in _NO_PREFERENCE_VALUES:
            status = "no_preference"
        elif normalized_value in _NOT_APPLICABLE_VALUES:
            status = "not_applicable"
    return EffectiveRequirement(
        key=candidate.key,
        label=candidate.label,
        value=value,
        status=status,
        blocking=False,
        blocking_reason="",
        aliases=candidate.aliases,
        classification="pending_verification",
        source_path=None,
        condition_state="unclassified",
    )


def _trusted_values(
    *,
    user_answers: list[Any],
    initial_input: str,
    named_inputs: dict[str, Any],
    prior_results: list[dict[str, Any]],
    previous_result: Any,
) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    trusted: dict[str, tuple[str, str]] = {}
    users: dict[str, str] = {}
    for reply in user_answers:
        answers = getattr(reply, "answers", None)
        if not isinstance(answers, dict) and isinstance(reply, dict):
            answers = reply.get("answers")
        if not isinstance(answers, dict):
            continue
        for key, value in answers.items():
            if (
                isinstance(key, str)
                and isinstance(value, str)
                and value.strip()
                and _normalize(value) not in _UNKNOWN_VALUES
            ):
                users[key] = value.strip()
                trusted[key] = (value.strip(), "user_answer")

    try:
        initial = json.loads(initial_input)
    except (TypeError, ValueError):
        initial = None
    if isinstance(initial, dict):
        for key, value in initial.items():
            if (
                isinstance(key, str)
                and _scalar_value(value)
                and _normalize(_string_value(value)) not in _UNKNOWN_VALUES
            ):
                trusted.setdefault(key, (_string_value(value), "user_input"))

    def add_named(values: dict[str, Any], prefix: str = "") -> None:
        for key, value in values.items():
            if not isinstance(key, str):
                continue
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                add_named(value, path)
            elif _scalar_value(value):
                rendered = _string_value(value)
                if _normalize(rendered) not in _UNKNOWN_VALUES:
                    trusted.setdefault(path, (rendered, "bound_input"))
                    trusted.setdefault(key, (rendered, "bound_input"))

    add_named(named_inputs)
    if isinstance(previous_result, dict):
        add_named(previous_result)
    for result in prior_results:
        if not isinstance(result, dict):
            continue
        for field in ("structured_result", "ai_verified_result"):
            value = result.get(field)
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except (TypeError, ValueError):
                    continue
            if isinstance(value, dict):
                add_named(value)
    return trusted, users


def _evaluate_condition(
    condition: Any,
    trusted_values: dict[str, tuple[str, str]],
) -> str:
    if not isinstance(condition, dict):
        return "unresolved"
    field = condition.get("field")
    actual = trusted_values.get(field) if isinstance(field, str) else None
    if actual is None:
        return "unresolved"
    actual_value = actual[0]
    operator = condition.get("operator")
    expected = condition.get("value")
    if operator == "present":
        return "active" if actual_value.strip() else "inactive"
    if operator == "equals":
        return "active" if _normalize(actual_value) == _normalize(_string_value(expected)) else "inactive"
    if operator == "not_equals":
        return "inactive" if _normalize(actual_value) == _normalize(_string_value(expected)) else "active"
    if operator == "in" and isinstance(expected, list):
        values = {_normalize(_string_value(item)) for item in expected}
        return "active" if _normalize(actual_value) in values else "inactive"
    return "unresolved"


def _validate_unique_requirements(requirements: list[dict[str, Any]]) -> None:
    seen: dict[str, dict[str, Any]] = {}
    for rule in requirements:
        previous = seen.get(rule["key"])
        if previous is None:
            seen[rule["key"]] = rule
            continue
        previous_contract = {
            key: previous.get(key)
            for key in (
                "key",
                "label",
                "classification",
                "reason",
                "condition",
                "condition_state",
            )
        }
        current_contract = {
            key: rule.get(key)
            for key in (
                "key",
                "label",
                "classification",
                "reason",
                "condition",
                "condition_state",
            )
        }
        if previous_contract != current_contract:
            raise ValueError(
                f"Conflicting frozen information requirements for {rule['key']!r}."
            )


def _scalar_value(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) and not isinstance(value, complex)


def _string_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


_NO_PREFERENCE_VALUES = {"随便", "无偏好", "no preference", "any"}
_NOT_APPLICABLE_VALUES = {
    "不需要",
    "不适用",
    "不涉及",
    "n/a",
    "na",
    "not applicable",
}
_UNKNOWN_VALUES = {
    "不知道",
    "不清楚",
    "未知",
    "待核实",
    "待指定",
    "未提供",
    "未确认",
    "unknown",
    "not known",
}

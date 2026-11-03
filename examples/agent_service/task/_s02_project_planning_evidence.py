# -*- coding: utf-8 -*-
"""S02 project-planning retrieval and report-evidence policy.

The rules in this module describe the project-planning scenario only. Generic
Task provenance and exact-citation checks live in ``_citation_verifier``.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from ._citation_verifier import (
    extract_task_citation_ids,
    strip_task_citation_ids,
    unique_json_object,
    verify_task_citations,
)
from ._evidence_contract import (
    detect_claim_evidence_protocol,
    normalize_evidence_snapshot,
    validate_claim_evidence_document,
)
from ._scenario_evidence_policies import get_scenario_evidence_policy

PLANNING_TEMPLATE_SEARCH_QUERIES = (
    "策划报告编制模板",
    "报告八章大纲",
    "策划报告写作要求",
    "项目策划模板 B-01 B-15",
)
PLANNING_TEMPLATE_NO_MATCH_MESSAGE = "未检索到匹配的策划模板。"
PLANNING_TEMPLATE_REJECTION_REASON = "检索命中文档未满足策划模板来源、八章结构及 B-01～B-15 原文校验。"
PROJECT_MATERIAL_SEARCH_QUERIES = (
    "项目委托 项目概况 项目背景 建设目标",
    "项目面积 建筑面积 设备台账 数量",
    "项目预算 投资估算 建设周期 计划工期",
)

_PROJECT_MATERIAL_TITLE_TERMS = (
    "委托",
    "需求说明",
    "项目资料",
    "项目概况",
    "项目任务书",
    "项目建议书",
    "项目申请",
    "立项材料",
)
_PROJECT_MATERIAL_ENGLISH_TITLE_PATTERN = re.compile(
    r"(?<![a-z0-9])project[-_\s]+"
    r"(?:charter|brief|requirements?|scope|assignment)(?![a-z0-9])",
    re.IGNORECASE,
)
_PROJECT_MATERIAL_EXCLUDED_TITLE_TERMS = (
    "模板",
    "历史案例",
    "会议纪要",
    "上传指南",
    "操作指南",
)
_PROJECT_MATERIAL_CONTENT_TERMS = (
    "项目背景",
    "项目概况",
    "项目名称",
    "建设目标",
    "项目目标",
    "建筑面积",
    "项目面积",
    "设备台账",
    "设备数量",
    "项目预算",
    "投资估算",
    "建设周期",
    "项目周期",
    "计划工期",
    "项目地点",
)
_PROJECT_MATERIAL_FACT_TERMS = (
    "建筑面积",
    "项目面积",
    "设备台账",
    "设备数量",
    "项目预算",
    "投资估算",
    "建设周期",
    "项目周期",
    "计划工期",
    "项目地点",
)

_PLANNING_TEMPLATE_SOURCE_EXCLUSIONS = (
    "需求资料",
    "历史案例",
    "会议纪要",
    "上传指南",
    "用户任务输入",
    "前期调研",
)
_PLANNING_TEMPLATE_CHAPTER_PATTERN = re.compile(
    r"(?:第\s*)?(一|二|三|四|五|六|七|八|[1-8])\s*章",
)
_PLANNING_TEMPLATE_RULE_PATTERN = re.compile(
    r"(?im)^\s*(?:[-*+•]\s*)?(?:\|\s*)?"
    r"B\s*[-‐‑‒–—~～]\s*(0[1-9]|1[0-5])(?!\d)"
    r"(?:\s*[：:、.．)）]\s*|\s+)"
    r"(?P<description>\S[^\r\n]*?)\s*(?:\|\s*)?$",
)
_PLANNING_TEMPLATE_REQUIRED_RULES = set(range(1, 16))
_PLANNING_TEMPLATE_CHAPTER_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
}


def _planning_template_source_match(source: str) -> bool:
    """Require a real source title identifying a planning report template."""

    normalized = source.strip().casefold()
    if not normalized or normalized == "未知来源":
        return False
    if any(
        term in normalized for term in _PLANNING_TEMPLATE_SOURCE_EXCLUSIONS
    ):
        return False
    chinese_template_title = "模板" in normalized and (
        "策划" in normalized or "报告" in normalized
    )
    english_template_title = bool(
        re.search(
            r"(?<![a-z0-9])planning[-_\s]+template(?![a-z0-9])",
            normalized,
        ),
    )
    return chinese_template_title or english_template_title


def _planning_template_content_match(
    evidence: list[dict[str, Any]],
) -> tuple[bool, dict[str, Any], str]:
    """Check outline and writing rules across chunks from one source."""

    ordered = sorted(
        evidence,
        key=lambda item: (int(item["chunk_index"]), item["content_sha256"]),
    )
    original_text = "\n".join(item["source_text"] for item in ordered)
    chapter_numbers = set()
    for match in _PLANNING_TEMPLATE_CHAPTER_PATTERN.findall(original_text):
        chapter_numbers.add(
            _PLANNING_TEMPLATE_CHAPTER_NUMBERS[match]
            if match in _PLANNING_TEMPLATE_CHAPTER_NUMBERS
            else int(match)
        )
    rule_numbers = {
        int(match.group(1))
        for match in _PLANNING_TEMPLATE_RULE_PATTERN.finditer(original_text)
        if match.group("description").strip()
    }
    outline_pattern = (
        r"(?:八章|8\s*章).{0,12}(?:大纲|结构|目录)|" r"(?:大纲|结构|目录).{0,12}(?:八章|8\s*章)"
    )
    has_outline_marker = bool(re.search(outline_pattern, original_text))
    has_writing_requirements = any(
        term in original_text
        for term in ("写作要求", "编写要求", "编制要求", "使用要求", "写作规范")
    )
    diagnostics = {
        "chapter_numbers": sorted(chapter_numbers),
        "writing_rule_ids": [
            f"B-{number:02d}" for number in sorted(rule_numbers)
        ],
        "has_outline_marker": has_outline_marker,
        "has_writing_requirements": has_writing_requirements,
    }
    if chapter_numbers != set(range(1, 9)):
        return False, diagnostics, "授权原文未完整支持八章大纲及章节结构。"
    if (
        not has_writing_requirements
        or rule_numbers != _PLANNING_TEMPLATE_REQUIRED_RULES
    ):
        return False, diagnostics, "授权原文未完整覆盖 B-01～B-15 编写要求。"
    return True, diagnostics, ""


def _project_material_source_match(
    source: str,
    source_text: str,
    *,
    selected: bool,
) -> bool:
    """Recognize project brief material from real title and chunk metadata.

    A selected document ID narrows authorization scope, but it is not by
    itself proof that the retrieved chunk contains project facts. The
    document title/content still have to match the project-material intent.
    """

    normalized_source = source.casefold()
    if any(
        term in normalized_source
        for term in _PROJECT_MATERIAL_EXCLUDED_TITLE_TERMS
    ):
        return False
    title_match = any(
        term in normalized_source for term in _PROJECT_MATERIAL_TITLE_TERMS
    ) or bool(_PROJECT_MATERIAL_ENGLISH_TITLE_PATTERN.search(normalized_source))
    content_match = any(
        term in source_text for term in _PROJECT_MATERIAL_CONTENT_TERMS
    )
    fact_match = any(
        term in source_text for term in _PROJECT_MATERIAL_FACT_TERMS
    )
    return content_match and fact_match and (title_match or selected)


_EVIDENCE_CLAIMS_BLOCK = re.compile(
    r"```task-evidence-claims[ \t]*\r?\n"
    r"(.*?)\r?\n```[ \t]*(?=\r?\n|$)",
    flags=re.DOTALL,
)
_CLAIM_EVIDENCE_BLOCK = re.compile(
    r"```task-claim-evidence[ \t]*\r?\n"
    r"(.*?)\r?\n```[ \t]*(?=\r?\n|$)",
    flags=re.DOTALL,
)
_CITATION_ID = re.compile(r"^C[1-9][0-9]{0,2}$")
_B_RULE_MARKER = re.compile(
    r"(?<![A-Za-z0-9])B\s*[-‐‑‒–—~～]\s*(0[1-9]|1[0-5])(?!\d)",
    re.IGNORECASE,
)
_TEMPLATE_RULE_MARKER = re.compile(
    r"模板(?:要求|规定|规则|标准|格式)|按模板|根据模板|" r"策划模板|报告八章大纲",
)
_PROJECT_FACT_MARKER = re.compile(
    r"面积|设备|台账|预算|投资|周期|工期|万元|平方米|㎡|\d+(?:\.\d+)?%?"
)
_PROJECT_FACT_VALUE = re.compile(
    r"(?:项目建筑面积|建筑面积|项目面积|面积|设备台账(?:数量)?|设备数量|"
    r"项目预算|预算|投资估算|建设周期|项目周期|周期|工期|项目地点)"
    r"\s*[:：为是约]?\s*"
    r"\d+(?:\.\d+)?\s*(?:平方米|平米|㎡|亩|台|套|个|万元|元|亿|月|周|天|年|公里|米)?"
)
_PROJECT_FACT_SCOPE = re.compile(
    r"项目(?:建筑)?面积|建筑面积|设备台账|设备数量|项目预算|"
    r"投资估算|建设周期|项目周期|项目工期|项目地点|预算(?:为|是|：|:)"
)
_PENDING_MARKERS = (
    "待核实",
    "待补充",
    "待确认",
    "建议",
    "拟定目标",
    "推断",
    "需人工复核",
)
_UNVERIFIED_STATUS_LABELS = {
    "pending_verification": "待核实",
    "pending_supplement": "待补充",
    "pending_confirmation": "待确认",
}
_MAX_CITATIONS = 64


def _materialize_s02_claim_evidence_ledger(
    document: str,
    evidence: list[dict[str, Any]],
    *,
    authorization_subject: str | None,
    template_version: str,
) -> dict[str, Any]:
    """Apply S02 policy to the source-neutral Claim/Evidence contract."""

    diagnostics: dict[str, list[dict[str, Any]]] = {
        "structure": [],
        "source": [],
        "citation": [],
        "unverified": [],
    }
    blocks = list(_CLAIM_EVIDENCE_BLOCK.finditer(document))
    evidence_snapshot = normalize_evidence_snapshot(
        evidence,
        authorization_subject=authorization_subject,
    )
    citation_check = verify_task_citations(
        document,
        evidence_snapshot,
        authorization_subject=authorization_subject,
    )
    contract = validate_claim_evidence_document(
        document,
        evidence_snapshot,
        authorization_subject=authorization_subject,
        citation_verification=citation_check,
        policy=get_scenario_evidence_policy(
            "s02_project_planning",
            template_version,
        ),
    )
    for category, items in contract["diagnostics"].items():
        diagnostics[category].extend(items)

    if detect_claim_evidence_protocol(document) == "mixed":
        _s02_add_diagnostic(
            diagnostics,
            "structure",
            "multiple_claim_protocols",
            None,
            None,
            [],
            "最终策划输出不能混用新旧 Claim 协议。",
        )
    if "来源与证据台账" in document:
        _s02_add_diagnostic(
            diagnostics,
            "structure",
            "model_supplied_ledger",
            None,
            None,
            [],
            "来源与证据台账由 Task 服务端生成，模型输出不得预置同名台账。",
        )
    if not contract["passed"] or any(diagnostics.values()):
        return _s02_failure(
            document,
            diagnostics,
            [],
            verification_scope=contract.get("verification_scope"),
        )

    claim_block = blocks[0]
    body = document[: claim_block.start()]

    evidence_by_id = {
        str(item["evidence_id"]): item for item in evidence_snapshot
    }
    verified_by_id = {
        item["citation_id"]: item
        for item in citation_check["verified_citations"]
    }
    bindings_by_position: dict[
        tuple[str, str | None], list[dict[str, Any]]
    ] = {}
    for binding in contract["bindings"]:
        bindings_by_position.setdefault(
            (binding["claim_id"], binding.get("occurrence_id")), []
        ).append(binding)
    claims_by_id = {item["claim_id"]: item for item in contract["claims"]}
    anchor_ranges = _claim_anchor_ranges(body, contract["claims"])
    occurrence_claims: list[dict[str, Any]] = []
    for claim in contract["claims"]:
        occurrences = claim.get("occurrences") or [
            {
                "occurrence_id": None,
                "text": claim["text"],
                "citation_ids": claim["citation_ids"],
            }
        ]
        for occurrence in occurrences:
            occurrence_id = occurrence.get("occurrence_id")
            occurrence_claims.append(
                {
                    **claim,
                    "occurrence_id": occurrence_id,
                    "text": occurrence["text"],
                    "citation_ids": occurrence["citation_ids"],
                    "anchor": (
                        f"{claim['claim_id']}:{occurrence_id}"
                        if occurrence_id
                        else claim["anchor"]
                    ),
                }
            )

    ledger: list[dict[str, Any]] = []
    pending_claims: list[tuple[str, str, str]] = []
    for claim in occurrence_claims:
        claim_id = claim["claim_id"]
        occurrence_id = claim.get("occurrence_id")
        claim_text = claim["text"].strip()
        position = anchor_ranges.get((claim_id, occurrence_id))
        section = _section_for_offset(body, position[0] if position else 0)
        claim_bindings = bindings_by_position.get(
            (claim_id, occurrence_id), []
        )
        sources = [
            evidence_by_id[binding["evidence_id"]]
            for binding in claim_bindings
            if binding["evidence_id"] in evidence_by_id
        ]

        if claim["kind"] == "unverified":
            continue

        rule_ids = {
            f"B-{int(number):02d}"
            for number in _B_RULE_MARKER.findall(claim_text)
        }
        typed_s02_protocol = template_version in {
            "3.2.0",
            "4.0.0",
            "4.1.0",
        }
        template_rule_claim = (
            claim["kind"] == "rule"
            if typed_s02_protocol
            else bool(rule_ids or _TEMPLATE_RULE_MARKER.search(claim_text))
        )
        template_limitation_claim = (
            typed_s02_protocol and claim["kind"] == "template_limitation"
        )
        explicit_recommendation = claim["kind"] == "recommendation" and any(
            marker in claim_text for marker in ("建议", "拟定目标", "推断")
        )
        if claim["kind"] == "recommendation" and not explicit_recommendation:
            _s02_add_diagnostic(
                diagnostics,
                "citation",
                "recommendation_not_marked",
                claim_id,
                claim["anchor"],
                [item["evidence_id"] for item in sources],
                "建议或拟定目标必须在正文中明确标记。",
            )

        project_fact = bool(
            _PROJECT_FACT_VALUE.search(claim_text)
            or (
                _PROJECT_FACT_SCOPE.search(claim_text)
                and not template_rule_claim
            )
        )
        if (
            project_fact
            and not explicit_recommendation
            and not any(
                item.get("evidence_type") == "project_material"
                for item in sources
            )
        ):
            _s02_add_diagnostic(
                diagnostics,
                "source",
                "project_fact_source_required",
                claim_id,
                claim["anchor"],
                [item["evidence_id"] for item in sources],
                "该项目事实必须关联本次检索的项目委托资料，不能用策划模板替代。",
            )
        if template_rule_claim and not all(
            item.get("evidence_type") == "planning_template"
            for item in sources
        ):
            _s02_add_diagnostic(
                diagnostics,
                "source",
                "template_rule_source_required",
                claim_id,
                claim["anchor"],
                [item["evidence_id"] for item in sources],
                "所标注的模板规则只能关联策划模板原文。",
            )
        if template_rule_claim and not _has_s02_rule_quote(
            rule_ids,
            claim_bindings,
            evidence_by_id,
            verified_by_id,
        ):
            _s02_add_diagnostic(
                diagnostics,
                "source",
                "template_clause_quote_insufficient",
                claim_id,
                claim["anchor"],
                [item["evidence_id"] for item in sources],
                (
                    "所引用原文未在同一模板文档的绑定片段中包含 B 条款编号及具体内容。"
                    if rule_ids
                    else "模板规则没有关联可追溯的策划模板原文。"
                ),
            )

        if template_limitation_claim and not all(
            item.get("evidence_type") == "planning_template"
            for item in sources
        ):
            _s02_add_diagnostic(
                diagnostics,
                "source",
                "template_limitation_source_required",
                claim_id,
                claim["anchor"],
                [item["evidence_id"] for item in sources],
                "模板适用性说明只能关联本次授权策划模板原文。",
            )

        for binding in claim_bindings:
            citation = verified_by_id.get(binding["citation_id"])
            source = evidence_by_id.get(binding["evidence_id"])
            if citation is None or source is None:
                continue
            ledger.append(
                {
                    "section": section,
                    "claim": claim_text,
                    "claim_id": claim_id,
                    "anchor": claim["anchor"],
                    "citation_id": binding["citation_id"],
                    **citation,
                    "quote": binding["quote"],
                    "status": "已落条",
                    "status_detail": "授权来源身份与该 claim 的逐字摘录已核验；语义支持需人工复核。",
                },
            )

    for record in contract["unverified_items"]:
        claim_id = record["claim_id"]
        occurrence_id = record.get("occurrence_id")
        claim = claims_by_id[claim_id]
        occurrence = next(
            (
                item
                for item in claim.get("occurrences", [])
                if item["occurrence_id"] == occurrence_id
            ),
            None,
        )
        claim_text = (
            occurrence["text"] if occurrence is not None else claim["text"]
        ).strip()
        pending_claims.append((claim_text, claim_id, record["unverified_id"]))
        display_status = _UNVERIFIED_STATUS_LABELS[record["status"]]
        position = anchor_ranges.get((claim_id, occurrence_id))
        ledger.append(
            {
                "section": _section_for_offset(
                    body, position[0] if position else 0
                ),
                "claim": claim_text,
                "claim_id": claim_id,
                "unverified_id": record["unverified_id"],
                "anchor": (
                    f"{claim_id}:{occurrence_id}"
                    if occurrence_id
                    else claim["anchor"]
                ),
                "citation_id": "—",
                "knowledge_base_name": "—",
                "source": "—",
                "quote": "—",
                "status": display_status,
                "status_detail": (
                    record["reason"]
                    + "；需补充证据类型："
                    + "、".join(record["expected_evidence_types"])
                    + f"；状态：{display_status}。"
                ),
            },
        )

    body_lines = body.splitlines()
    for line_number, line in enumerate(body_lines, start=1):
        stripped = line.strip()
        table_separator = bool(re.fullmatch(r"\|?[\s:|-]+\|?", stripped))
        if not stripped or stripped.startswith("#") or table_separator:
            continue
        next_line = (
            body_lines[line_number].strip()
            if line_number < len(body_lines)
            else ""
        )
        if stripped.startswith("|") and re.fullmatch(
            r"\|?[\s:|-]+\|?", next_line
        ):
            continue
        if not _PROJECT_FACT_MARKER.search(stripped):
            continue
        line_offset = _line_offset(body, line_number)
        covered = any(
            start <= line_offset < end for start, end in anchor_ranges.values()
        )
        if not covered:
            _s02_add_diagnostic(
                diagnostics,
                "citation",
                "project_fact_without_claim_anchor",
                None,
                f"line:{line_number}",
                [],
                "正文项目数据/事实行未关联独立 claim 锚点。",
            )

    has_project_evidence = any(
        item.get("evidence_type") == "project_material"
        for item in evidence_snapshot
    )
    if not has_project_evidence:
        for required_term in ("面积", "设备台账", "预算", "周期"):
            if not any(
                required_term in pending_claim
                for pending_claim, _, _ in pending_claims
            ):
                associated_claim = (
                    pending_claims[0] if pending_claims else None
                )
                _s02_add_diagnostic(
                    diagnostics,
                    "unverified",
                    "required_project_fact_pending",
                    associated_claim[1] if associated_claim else None,
                    (
                        claims_by_id[associated_claim[1]]["anchor"]
                        if associated_claim
                        else None
                    ),
                    [],
                    f"未检索到项目委托资料，‘{required_term}’必须明确列入待核实台账。",
                    unverified_id=(
                        associated_claim[2] if associated_claim else None
                    ),
                )

    if any(diagnostics.values()):
        return _s02_failure(
            document,
            diagnostics,
            ledger,
            verification_scope=contract.get("verification_scope"),
        )

    ledger_markdown = _render_s02_project_planning_ledger(
        ledger,
        evidence_snapshot,
    )
    replaced = (
        document[: claim_block.start()]
        + ledger_markdown
        + document[claim_block.end() :]
    )
    final_check = verify_task_citations(
        replaced,
        evidence_snapshot,
        authorization_subject=authorization_subject,
    )
    if not final_check["passed"]:
        for issue in final_check["issues"]:
            _s02_add_diagnostic(
                diagnostics,
                "citation",
                "final_citation_recheck_failed",
                None,
                None,
                [],
                str(issue),
            )
        return _s02_failure(
            document,
            diagnostics,
            ledger,
            verification_scope={
                **contract["verification_scope"],
                "final_citation_recheck": "failed",
            },
        )
    if len(replaced.encode("utf-8")) > 100_000:
        _s02_add_diagnostic(
            diagnostics,
            "structure",
            "ledger_document_too_large",
            None,
            None,
            [],
            "生成来源与证据台账后文档超过 100 KB 限制。",
        )
        return _s02_failure(
            document,
            diagnostics,
            ledger,
            verification_scope={
                **contract["verification_scope"],
                "final_citation_recheck": "passed",
            },
        )
    return {
        "passed": True,
        "issues": [],
        "diagnostics": diagnostics,
        "all_diagnostics": [],
        "document": replaced,
        "ledger": ledger,
        "verification_scope": {
            **contract["verification_scope"],
            "final_citation_recheck": "passed",
            "semantic_support": "requires_human_review",
        },
    }


def _has_s02_rule_quote(
    rule_ids: set[str],
    bindings: list[dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
    verified_by_id: dict[str, dict[str, Any]],
) -> bool:
    if not bindings:
        return False
    grouped: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for binding in bindings:
        citation = verified_by_id.get(binding["citation_id"])
        source = evidence_by_id.get(binding["evidence_id"])
        if (
            citation is None
            or source is None
            or source.get("evidence_type") != "planning_template"
        ):
            continue
        key = (
            str(citation.get("knowledge_base_id", "")),
            str(citation.get("document_id", "")),
        )
        grouped.setdefault(key, []).append(
            (int(citation["chunk_index"]), binding["quote"]),
        )
    required = set(rule_ids)
    for chunks in grouped.values():
        ordered = sorted(chunks, key=lambda item: item[0])
        for start in range(len(ordered)):
            combined: list[str] = []
            previous_index: int | None = None
            for index, quote in ordered[start:]:
                if previous_index is not None and index != previous_index + 1:
                    break
                combined.append(quote)
                text = "\n".join(combined)
                found = {
                    f"B-{int(number):02d}"
                    for number in _B_RULE_MARKER.findall(text)
                }
                substance = _B_RULE_MARKER.sub("", text).strip()
                if required.issubset(found) and len(substance) >= 8:
                    return True
                previous_index = index
    return False


def _claim_anchor_ranges(
    body: str,
    claims: list[dict[str, Any]],
) -> dict[tuple[str, str | None], tuple[int, int]]:
    lines = body.splitlines(keepends=True)
    offsets: list[tuple[int, int, str]] = []
    offset = 0
    for line in lines:
        offsets.append((offset, offset + len(line), line.rstrip("\r\n")))
        offset += len(line)
    ranges: dict[tuple[str, str | None], tuple[int, int]] = {}
    for claim in claims:
        claim_id = claim["claim_id"]
        occurrences = claim.get("occurrences") or [None]
        for occurrence in occurrences:
            occurrence_id = (
                occurrence.get("occurrence_id")
                if isinstance(occurrence, dict)
                else None
            )
            marker = (
                f"<!-- task-claim:{claim_id}:{occurrence_id} -->"
                if occurrence_id
                else f"<!-- task-claim:{claim_id} -->"
            )
            matches = list(re.finditer(re.escape(marker), body))
            if len(matches) != 1:
                continue
            marker_start = matches[0].start()
            line_index = next(
                (
                    i
                    for i, line in enumerate(offsets)
                    if line[0] <= marker_start < line[1]
                ),
                None,
            )
            if line_index is None:
                continue
            line_start, line_end, line_text = offsets[line_index]
            if line_text.lstrip().startswith("|") or line_text.rstrip().endswith(
                "|"
            ):
                ranges[(claim_id, occurrence_id)] = (line_start, line_end)
                continue
            first = line_index
            last = line_index
            while first > 0:
                previous = offsets[first - 1][2]
                if not previous.strip() or previous.lstrip().startswith("#"):
                    break
                first -= 1
            while last + 1 < len(offsets):
                following = offsets[last + 1][2]
                if not following.strip() or following.lstrip().startswith("#"):
                    break
                last += 1
            ranges[(claim_id, occurrence_id)] = (
                offsets[first][0],
                offsets[last][1],
            )
    return ranges


def _section_for_offset(body: str, offset: int) -> str:
    section = "未分节"
    for match in re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*$", body):
        if match.start() > offset:
            break
        section = match.group(1).strip()
    return section


def _line_offset(body: str, line_number: int) -> int:
    lines = body.splitlines(keepends=True)
    return sum(len(line) for line in lines[: line_number - 1])


def _s02_add_diagnostic(
    diagnostics: dict[str, list[dict[str, Any]]],
    category: str,
    error_code: str,
    claim_id: str | None,
    location: str | None,
    evidence_ids: list[str],
    message: str,
    *,
    unverified_id: str | None = None,
) -> None:
    diagnostics[category].append(
        {
            "error_code": error_code,
            "claim_id": claim_id,
            "unverified_id": unverified_id,
            "location": location,
            "error_type": category,
            "evidence_ids": evidence_ids,
            "message": message,
        },
    )


def _s02_failure(
    document: str,
    diagnostics: dict[str, list[dict[str, Any]]],
    ledger: list[dict[str, Any]],
    *,
    verification_scope: dict[str, str] | None = None,
) -> dict[str, Any]:
    all_diagnostics = [
        item for category in diagnostics.values() for item in category
    ]
    return {
        "passed": False,
        "issues": list(
            dict.fromkeys(item["message"] for item in all_diagnostics)
        ),
        "diagnostics": diagnostics,
        "all_diagnostics": all_diagnostics,
        "document": document,
        "ledger": ledger,
        "verification_scope": verification_scope
        or {"protocol_parse": "not_evaluated"},
    }


def materialize_s02_project_planning_ledger(
    document: str,
    evidence: list[dict[str, Any]],
    *,
    authorization_subject: str | None = None,
    template_version: str = "1.0.0",
) -> dict[str, Any]:
    """Select the structured contract or conservative legacy reader."""

    if detect_claim_evidence_protocol(document) in {
        "claim_evidence",
        "mixed",
    }:
        return _materialize_s02_claim_evidence_ledger(
            document,
            evidence,
            authorization_subject=authorization_subject,
            template_version=template_version,
        )
    result = _materialize_s02_legacy_ledger(
        document,
        evidence,
        authorization_subject=authorization_subject,
    )
    if result.get("passed"):
        result.setdefault(
            "diagnostics",
            {
                "structure": [],
                "source": [],
                "citation": [],
                "unverified": [],
            },
        )
        result.setdefault("all_diagnostics", [])
        return result
    return _add_legacy_diagnostics(result)


def _add_legacy_diagnostics(result: dict[str, Any]) -> dict[str, Any]:
    """Expose old-schema failures without inventing IDs absent from history."""

    groups: dict[str, list[dict[str, Any]]] = {
        "structure": [],
        "source": [],
        "citation": [],
        "unverified": [],
    }
    for index, issue in enumerate(result.get("issues", [])):
        message = str(issue)
        if "待核实" in message or "待补资料" in message:
            category = "unverified"
        elif "来源" in message or "原文" in message or "项目委托" in message:
            category = "source"
        elif "引用" in message or "编号" in message:
            category = "citation"
        else:
            category = "structure"
        position = re.search(r"(?:claims|unverified_items)\[(\d+)\]", message)
        groups[category].append(
            {
                "error_code": "legacy_compatibility_validation",
                "claim_id": None,
                "unverified_id": None,
                "location": (
                    f"{position.group(0)}"
                    if position
                    else f"legacy_issue:{index}"
                ),
                "error_type": category,
                "evidence_ids": [],
                "message": message,
            },
        )
    return {
        **result,
        "diagnostics": groups,
        "all_diagnostics": [
            item for category in groups.values() for item in category
        ],
    }


def _materialize_s02_legacy_ledger(
    document: str,
    evidence: list[dict[str, Any]],
    *,
    authorization_subject: str | None = None,
) -> dict[str, Any]:
    """Replace a model claim map with a server-derived source ledger.

    The model may identify which report sentences it intends to source, but
    it cannot choose verification status or source metadata. Those fields are
    reconstructed here from the persisted retrieval snapshot and exact quote
    checks. Semantic support remains explicitly pending human review.
    """

    blocks = list(_EVIDENCE_CLAIMS_BLOCK.finditer(document))
    if len(blocks) != 1:
        return {
            "passed": False,
            "issues": ["最终策划输出必须包含唯一 task-evidence-claims 对照区块。"],
            "document": document,
            "ledger": [],
        }
    if "来源与证据台账" in document:
        return {
            "passed": False,
            "issues": ["来源与证据台账由 Task 服务端生成，模型输出不得预置同名台账。"],
            "document": document,
            "ledger": [],
        }
    try:
        payload = json.loads(
            blocks[0].group(1),
            object_pairs_hook=unique_json_object,
        )
    except (json.JSONDecodeError, RecursionError, ValueError):
        return {
            "passed": False,
            "issues": ["task-evidence-claims 不是合法 JSON。"],
            "document": document,
            "ledger": [],
        }
    if not isinstance(payload, dict) or set(payload) != {
        "claims",
        "unverified_items",
    }:
        return {
            "passed": False,
            "issues": ["task-evidence-claims 必须包含 claims 和 unverified_items。"],
            "document": document,
            "ledger": [],
        }
    claims = payload["claims"]
    unverified_items = payload["unverified_items"]
    if (
        not isinstance(claims, list)
        or not isinstance(unverified_items, list)
        or len(claims) > _MAX_CITATIONS
        or len(unverified_items) > _MAX_CITATIONS
    ):
        return {
            "passed": False,
            "issues": ["claims 或 unverified_items 超出结构或数量限制。"],
            "document": document,
            "ledger": [],
        }

    citation_check = verify_task_citations(
        document,
        evidence,
        authorization_subject=authorization_subject,
    )
    if not citation_check["passed"]:
        return {
            "passed": False,
            "issues": citation_check["issues"],
            "document": document,
            "ledger": [],
        }

    body = document[: blocks[0].start()]
    verified_by_id = {
        item["citation_id"]: item
        for item in citation_check["verified_citations"]
    }
    issues: list[str] = []
    claimed_ids: dict[str, str] = {}
    quote_claims: dict[tuple[str, str, int, str], str] = {}
    ledger: list[dict[str, Any]] = []
    claim_fields = {"section", "claim", "claim_type", "citation_ids"}
    for index, item in enumerate(claims):
        prefix = f"claims[{index}]"
        if not isinstance(item, dict) or set(item) != claim_fields:
            issues.append(f"{prefix} 字段不符合证据映射协议。")
            continue
        section = item.get("section")
        claim = item.get("claim")
        claim_type = item.get("claim_type")
        citation_ids = item.get("citation_ids")
        if (
            not isinstance(section, str)
            or not section.strip()
            or len(section) > 160
            or section.strip() not in body
            or not isinstance(claim, str)
            or not claim.strip()
            or len(claim) > 2_000
            or claim not in body
            or not isinstance(claim_type, str)
            or claim_type
            not in {
                "project_fact",
                "template_rule",
                "recommendation",
                "other",
            }
            or not isinstance(citation_ids, list)
            or not citation_ids
            or len(citation_ids) > 8
            or any(
                not isinstance(citation_id, str)
                or not _CITATION_ID.fullmatch(citation_id)
                for citation_id in citation_ids
            )
            or len(set(citation_ids)) != len(citation_ids)
        ):
            issues.append(f"{prefix} 的章节、事实、类型或引用关联无效。")
            continue
        body_ids = set(extract_task_citation_ids(claim))
        if body_ids != set(citation_ids):
            issues.append(f"{prefix} 的引用编号与所列事实不一致。")
            continue
        rule_ids = {
            f"B-{int(number):02d}" for number in _B_RULE_MARKER.findall(claim)
        }
        template_rule_claim = bool(
            rule_ids or _TEMPLATE_RULE_MARKER.search(claim)
        )
        explicit_recommendation = claim_type == "recommendation" and any(
            marker in claim for marker in ("建议", "拟定目标", "推断")
        )
        if claim_type == "recommendation" and not explicit_recommendation:
            issues.append(f"{prefix} 的建议或拟定目标必须在正文中明确标记。")
        requires_project_source = not explicit_recommendation and (
            bool(_PROJECT_FACT_VALUE.search(claim))
            or (
                bool(_PROJECT_FACT_SCOPE.search(claim))
                and not template_rule_claim
            )
        )
        if requires_project_source and not any(
            citation_id in verified_by_id
            and verified_by_id[citation_id]["evidence_type"]
            == "project_material"
            for citation_id in citation_ids
        ):
            issues.append(f"{prefix} 的项目数值事实必须至少关联一条本次检索的项目委托资料。")
        if claim_type == "project_fact" and not all(
            citation_id in verified_by_id
            and verified_by_id[citation_id]["evidence_type"]
            == "project_material"
            for citation_id in citation_ids
        ):
            issues.append(f"{prefix} 声明为项目事实，但未关联项目委托资料。")
        if claim_type == "template_rule" and not all(
            citation_id in verified_by_id
            and verified_by_id[citation_id]["evidence_type"]
            == "planning_template"
            for citation_id in citation_ids
        ):
            issues.append(f"{prefix} 声明为模板条款，但未关联策划模板原文。")
        if template_rule_claim:
            has_clause_quote = any(
                citation_id in verified_by_id
                and verified_by_id[citation_id]["evidence_type"]
                == "planning_template"
                and rule_ids.issubset(
                    {
                        f"B-{int(number):02d}"
                        for number in _B_RULE_MARKER.findall(
                            verified_by_id[citation_id]["quote"]
                        )
                    },
                )
                and len(
                    _B_RULE_MARKER.sub(
                        "", verified_by_id[citation_id]["quote"]
                    ).strip()
                )
                >= 8
                for citation_id in citation_ids
            )
            if not has_clause_quote:
                issues.append(
                    f"{prefix} 所引用原文没有包含所标注 B 条款的具体内容。"
                    if rule_ids
                    else f"{prefix} 的模板规则没有关联真实策划模板原文。"
                )
        for citation_id in citation_ids:
            if citation_id not in verified_by_id:
                issues.append(f"{prefix} 引用了未通过来源校验的编号 {citation_id}。")
                continue
            prior_claim = claimed_ids.get(citation_id)
            if prior_claim is not None and prior_claim != claim:
                issues.append(f"引用 {citation_id} 被复用于不同事实，必须逐项提供来源摘录。")
                continue
            claimed_ids[citation_id] = claim
            verified = verified_by_id[citation_id]
            identity = (
                verified["knowledge_base_id"],
                verified["document_id"],
                verified["chunk_index"],
                verified["quote"],
            )
            previous = quote_claims.get(identity)
            if previous is not None and previous != claim:
                issues.append("同一来源摘录被用于不同事实，请提供分别对应的原文摘录。")
            quote_claims[identity] = claim
            ledger.append(
                {
                    "section": section.strip(),
                    "claim": strip_task_citation_ids(claim).strip(),
                    "citation_id": citation_id,
                    **verified,
                    "status": "已落条",
                    "status_detail": "来源身份与逐字摘录已核验；语义支持需人工复核。",
                },
            )

    if set(claimed_ids) != set(verified_by_id):
        missing = sorted(set(verified_by_id) - set(claimed_ids))
        issues.append("引用台账未逐项说明引用编号：" + "、".join(missing) + "。")
    marker_counts = Counter(extract_task_citation_ids(body))
    repeated_markers = sorted(
        citation_id
        for citation_id, count in marker_counts.items()
        if count != 1
    )
    if repeated_markers:
        issues.append(
            "引用编号被用于多个正文位置，不能由单条来源摘录覆盖：" + "、".join(repeated_markers) + "。"
        )

    for index, item in enumerate(unverified_items):
        prefix = f"unverified_items[{index}]"
        if (
            not isinstance(item, dict)
            or set(item) not in (
                {"section", "claim", "reason"},
                {"section", "claim", "reason", "status"},
            )
            or not all(
                isinstance(item.get(key), str) and item[key].strip()
                for key in ("section", "claim", "reason")
            )
            or len(item["section"]) > 160
            or item["section"].strip() not in body
            or len(item["claim"]) > 2_000
            or len(item["reason"]) > 1_000
            or item["claim"] not in body
            or item.get("status", "pending_verification")
            not in _UNVERIFIED_STATUS_LABELS
        ):
            issues.append(f"{prefix} 必须对应正文并提供有效的结构化待处理状态。")
            continue
        display_status = _UNVERIFIED_STATUS_LABELS[
            item.get("status", "pending_verification")
        ]
        ledger.append(
            {
                "section": item["section"].strip(),
                "claim": item["claim"].strip(),
                "citation_id": "—",
                "knowledge_base_name": "—",
                "source": "—",
                "quote": "—",
                "status": display_status,
                "status_detail": ("未附对应的授权原文证据；需补充资料并人工复核。"),
            },
        )

    clean_body = body.replace("\r\n", "\n")
    pending_claims = [
        (item["claim"], item.get("status", "pending_verification"))
        for item in unverified_items
        if isinstance(item, dict) and isinstance(item.get("claim"), str)
    ]
    for line in clean_body.splitlines():
        stripped = line.strip()
        table_separator = bool(re.fullmatch(r"\|?[\s:|-]+\|?", stripped))
        if (
            not stripped
            or stripped.startswith("#")
            or stripped.startswith("```")
            or table_separator
        ):
            continue
        if not _PROJECT_FACT_MARKER.search(stripped):
            continue
        registered_unverified = any(
            claim and claim in stripped for claim, _status in pending_claims
        )
        if registered_unverified:
            continue
        line_citation_ids = set(extract_task_citation_ids(stripped))
        if line_citation_ids and line_citation_ids.issubset(claimed_ids):
            # A citation on this line has already passed the claim mapping and
            # source checks above. Do not report it again as an unledgered
            # project fact merely because the template text mentions projects.
            continue
        if line_citation_ids or any(
            marker in stripped for marker in _PENDING_MARKERS
        ):
            issues.append(
                "正文中的项目事实或待处理事项未列入来源台账："
                + stripped[:160]
            )
            continue
        issues.append("正文包含未引用且未标记待核实的项目数据/事实行：" + stripped[:160])

    has_project_evidence = any(
        item.get("evidence_type") == "project_material"
        for item in evidence
        if isinstance(item, dict)
    )
    if not has_project_evidence:
        for required_pending_term in ("面积", "设备台账", "预算", "周期"):
            if not any(
                required_pending_term in claim
                for claim, _status in pending_claims
            ):
                issues.append(
                    f"未检索到项目委托资料，‘{required_pending_term}’必须明确登记为未解决事项。"
                )

    if issues:
        return {
            "passed": False,
            "issues": issues,
            "document": document,
            "ledger": ledger,
        }

    ledger_markdown = _render_s02_project_planning_ledger(ledger, evidence)
    claims_block = blocks[0]
    replaced = (
        document[: claims_block.start()]
        + ledger_markdown
        + document[claims_block.end() :]
    )
    # The citation block is still the final block; a second deterministic
    # pass below confirms no content was changed outside the generated ledger.
    final_check = verify_task_citations(
        replaced,
        evidence,
        authorization_subject=authorization_subject,
    )
    if not final_check["passed"]:
        return {
            "passed": False,
            "issues": final_check["issues"],
            "document": document,
            "ledger": ledger,
        }
    if len(replaced.encode("utf-8")) > 100_000:
        return {
            "passed": False,
            "issues": ["生成来源与证据台账后文档超过 100 KB 限制。"],
            "document": document,
            "ledger": ledger,
        }
    return {
        "passed": True,
        "issues": [],
        "document": replaced,
        "ledger": ledger,
        "verification_scope": final_check["verification_scope"],
    }


def _render_s02_project_planning_ledger(
    ledger: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> str:
    """Render status and source fields from verifier-owned values."""

    project_count = sum(
        1
        for item in evidence
        if item.get("evidence_type") == "project_material"
    )
    project_status = (
        f"已保存 {project_count} 个项目资料原文片段。"
        if project_count
        else "未检索到匹配的项目委托资料；项目事实均须标注待核实。"
    )
    rows = [
        "",
        "### 来源与证据台账",
        "",
        f"项目资料检索状态：{project_status}",
        "",
        "| 章节或事实 | 引用编号 | 资料名称 | 来源片段 | 核验状态 | 未通过原因或人工复核事项 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in ledger:
        citation_id = item.get("citation_id", "—")
        source_name = item.get("source") or "—"
        if item.get("document_id"):
            source_name += (
                f"（知识库：{item.get('knowledge_base_name') or '—'}；"
                f"文档ID：{item['document_id']}；"
                f"片段：{int(item.get('chunk_index', 0)) + 1}；"
                f"SHA-256：{item.get('content_sha256') or '—'}；"
                f"Evidence ID：{item.get('evidence_id') or '—'}）"
            )
        claim_id = item.get("claim_id") or item.get("unverified_id")
        claim_label = (
            (f"[{claim_id}] " if claim_id else "")
            + item.get("section", "—")
            + "："
            + item.get("claim", "—")
        )
        rows.append(
            "| "
            + " | ".join(
                _escape_table_cell(value)
                for value in (
                    claim_label,
                    citation_id,
                    source_name,
                    item.get("quote", "—"),
                    item.get("status", "待核实"),
                    item.get("status_detail", "需人工复核。"),
                )
            )
            + " |",
        )
    rows.extend(
        [
            "",
            "说明：‘已落条’仅表示引用编号、授权来源身份和逐字摘录通过确定性核验；" "原文是否在语义上支持该结论尚未自动判定，须人工复核。",
            "",
        ],
    )
    return "\n".join(rows)


def _escape_table_cell(value: Any) -> str:
    text = str(value or "—").replace("\r", " ").replace("\n", " ")
    return text.replace("|", "\\|")


__all__ = [
    "materialize_s02_project_planning_ledger",
]

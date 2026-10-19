# -*- coding: utf-8 -*-
"""Backward-compatible Task claim/evidence association protocol.

This module owns source-neutral evidence identity and claim-to-source
association. Scenario-specific policy belongs in scenario adapters.
"""

from __future__ import annotations

import json
import hashlib
import re
from difflib import SequenceMatcher
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    model_validator,
)

from ._citation_verifier import (
    DuplicateJSONKeyError,
    _task_evidence_id,
    extract_task_citation_ids,
    unique_json_object,
    verify_task_citations,
)

_CLAIM_BLOCK = re.compile(
    r"```task-claim-evidence[ \t]*\r?\n"
    r"(.*?)\r?\n```[ \t]*(?=\r?\n|$)",
    flags=re.DOTALL,
)
_CITATIONS_BLOCK = re.compile(
    r"```task-citations[ \t]*\r?\n"
    r"(.*?)\r?\n```[ \t]*(?=\r?\n|$)",
    flags=re.DOTALL,
)
_CLAIM_BLOCK_START = re.compile(r"```task-claim-evidence(?=[ \t\r\n]|$)")
_CITATION_BLOCK_START = re.compile(r"```task-citations(?=[ \t\r\n]|$)")
_LEGACY_CLAIM_BLOCK_START = re.compile(
    r"```task-evidence-claims(?=[ \t\r\n]|$)"
)
_CLAIM_ANCHOR = re.compile(
    r"<!-- task-claim:(CL-[A-Za-z0-9][A-Za-z0-9_-]{0,44})"
    r"(?::(O-[A-Za-z0-9][A-Za-z0-9_-]{0,44}))? -->"
)
_CLAIM_ID = re.compile(r"^CL-[A-Za-z0-9][A-Za-z0-9_-]{0,44}$")
_CITATION_ID = re.compile(r"^C[1-9][0-9]{0,2}$")
_NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:,\d{3})*(?:\.\d+)?")
_IDENTIFIER = re.compile(r"[A-Za-z]+[-_ ]?\d+")
_HAN_RUN = re.compile(r"[\u3400-\u9fff]{2,}")
_STOP_TERMS = {
    "项目",
    "本项目",
    "报告",
    "方案",
    "内容",
    "依据",
    "根据",
    "相关",
    "当前",
    "本次",
    "结果",
    "信息",
    "资料",
    "情况",
    "事项",
    "工作",
    "说明",
    "明确",
    "提供",
    "进行",
    "要求",
    "需要",
    "以及",
    "可以",
}
_MAX_ITEMS = 128
_MAX_DOCUMENT_BYTES = 100_000
_MAX_SOURCE_TEXT_CHARS = 8_000
_MAX_QUOTE_CHARS = 2_000
_MAX_PROTOCOL_REPAIR_BODY_CHARS = 16_000

TASK_CLAIM_EVIDENCE_PROMPT = """Task 通用 Claim–Evidence 输出协议（严格按当前 Schema）：
正文中每个需要来源支持的事实/规则，以及每个待核实事项，都要有稳定锚点；每个段落或表格行最多一个 occurrence，格式为 `<!-- task-claim:CL-name:O-place -->`。锚点必须与它标记的正文内容处于同一物理行：段落锚点紧接该段末尾，表格锚点放在该行末尾；不得把锚点单独放到下一行，也不得在一段正文后连续放多个锚点。正确示例：`项目编号为 DEMO-001 [C1]。<!-- task-claim:CL-01:O-01-1 -->`。错误示例：正文结束后另起一行写锚点，或在同一段后连续写两个锚点。Occurrence.text 必须对应锚点所在的实际段落或表格行；正文引用标记必须与 occurrence.citation_ids 完全一致。同一段落或表格行若混合有来源支持的事实和待核实事项，先拆成独立段落或表格行，各自只放一个锚点；`kind=unverified` 的 occurrence 必须没有任何正文引用标记。不能用一个锚点覆盖整张含有不同事实、来源或状态的表格。
同一 Claim 的所有 occurrence 必须属于相同 kind。凡正文 occurrence 含 `[C…]` 引用标记，不得声明为 `kind=unverified`；凡待核实陈述不得带引用标记。若一个句子同时含有可引用陈述和待核实内容，拆成两个独立段落、两个 Claim 和两个锚点。每个 unverified occurrence 必须恰好有一条 `unverified_items` 记录，且 `claim_id`、`occurrence_id` 与正文锚点完全一致；不能漏掉同一 Claim 的重复出现位置。
正文后只输出一个 `task-claim-evidence` JSON 区块，顶层且仅含 `claims`、`bindings`、`unverified_items`。使用紧凑单行 JSON，不缩进，不重复报告正文或手工生成来源台账。字段必须严格如下，不得添加 `section` 等额外字段：Claim 为 `claim_id`、`kind`（fact/rule/historical_case/analysis/recommendation/unverified）、`anchor`（与 claim_id 相同）、`text`、`citation_ids`、`occurrences`；Occurrence 为 `occurrence_id`、`text`、`citation_ids`；Binding 为 `claim_id`、`occurrence_id`、`citation_id`、本次 TaskRun 授权快照中的 `evidence_id`、连续逐字 `quote`；Unverified 为 `unverified_id`、`claim_id`、`occurrence_id`、`status`（pending_verification/pending_supplement/pending_confirmation）、`reason`、`expected_evidence_types`。每个 occurrence/citation 组合各有一个 binding；claim.citation_ids 必须等于其 occurrences.citation_ids 的并集。事实、规则和历史案例没有来源时必须声明为 unverified；纯分析或建议可在明确标注且不冒充来源事实时使用空 citation_ids。每个独立待核实事项都登记一个 unverified_id。
每条 binding 必须针对它自己的 occurrence 选择来源中最短且相关的连续逐字摘录；不能把一条宽泛引用摘录复制给多个不同声明来代替独立核对。摘录应包含该声明的关键用语及其项目编号、数值等重要标识；来源中找不到相应原文时，不要引用该来源，也不得改写、拼接或补造原文。`task-citations` 是每个引用编号一条的来源索引；它不能替代逐 occurrence 的 `bindings`。
UnverifiedRecord.status 是结构化业务状态：`pending_verification`=待核实（来源或事实尚未核实），`pending_supplement`=待补充（明确缺少资料），`pending_confirmation`=待确认（等待责任人作出确认/决定）。每个状态都仍是未解决状态，不代表核验通过；历史记录省略 status 时服务端按 `pending_verification` 兼容读取。服务端以 UnverifiedRecord 和 Claim kind 判断是否未解决，不以正文关键词判断事实是否已核实。
若节点原有协议要求输出 `task-citations`，必须按该协议提供。Task 通用 task-verifier 会结合本次运行的实际输入、前序结果和授权检索内容评估引用及来源说明；模型生成的编号、摘录和通过声明不构成服务端核验结果。知识库访问范围仍由 Task 检索边界强制执行。语义支持仍需人工复核。"""


TASK_CLAIM_EVIDENCE_REPAIR_PROMPT = """Task Claim–Evidence 协议修复专用响应：
本次调用只生成紧凑 JSON 元数据，不重写或复述报告正文。正文已由服务端锁定；Occurrence 文本、锚点和引用编号将从原正文确定性提取。
顶层必须且仅包含 `claims`、`citations`、`bindings`、`unverified_items` 四个数组字段。`claims` 每项仅含 `claim_id` 和 `kind`，kind 仅可为 fact、rule、historical_case、analysis、recommendation、unverified；claim_id 必须对应正文已有的 CL 锚点，不能新增或省略。`citations` 每个引用编号恰好一项，仅含 `citation_id`、当前 TaskRun 实际检索证据中的 `evidence_id` 和连续逐字 `quote`；编号集合必须与正文完全一致。`bindings` 对正文中每个 occurrence/citation 组合各提供一条记录，仅含 `claim_id`、`occurrence_id`、`citation_id`、对应 citation 的 `evidence_id` 和针对该 occurrence 的连续逐字 `quote`；不可将一个引用的宽泛 quote 复制给多个不同 occurrence。`unverified_items` 每个无来源 occurrence 一项，含 `unverified_id`、`claim_id`、`occurrence_id`、`status`、`reason`、`expected_evidence_types`；status 必须按原值保留，不得把“待补充/待确认”改成其它状态。每个锚点 occurrence 必须只表达一种可核验状态；若同一 occurrence 同时包含有引用的事实和待核实事项，或 `kind=unverified` 的 occurrence 带有任何正文引用标记，则该正文无法通过仅修协议元数据来修复，应拒绝修复并由后续报告生成拆分 occurrence。不要输出代码围栏、正文、额外字段、校验结论或虚构信息。服务端将逐条检查来源身份、权限、哈希和原文，语义支持仍需人工复核。"""

TASK_CLAIM_EVIDENCE_REPAIR_LOCKED_CITATIONS_PROMPT = """Task Claim–Evidence 协议修复专用响应：
本次调用只生成紧凑 JSON 元数据，不重写或复述报告正文。正文和先前已完整输出的 task-citations JSON 区块已由服务端锁定；服务端会按协议把引用区块放在最终输出末尾。Occurrence 文本、锚点和引用编号将从锁定正文确定性提取。
顶层必须且仅包含 claims、bindings 和 unverified_items 三个数组字段。claims 每项仅含 claim_id 和 kind，kind 仅可为 fact、rule、historical_case、analysis、recommendation、unverified；claim_id 必须对应正文已有的 CL 锚点，不能新增或省略。bindings 对正文中每个 occurrence/citation 组合各提供一条记录，仅含 claim_id、occurrence_id、citation_id、该编号在锁定 task-citations 中的 evidence_id 和针对该 occurrence 的连续逐字 quote。不能把锁定的单条 citation quote 自动复制为多条 occurrence binding；quote 必须是锁定授权证据原文中与该 occurrence 相关的连续片段。无法确定相应片段时不要伪造绑定，修复应失败关闭。unverified_items 每个无来源 occurrence 一项，含 unverified_id、claim_id、occurrence_id、status、reason、expected_evidence_types；status 必须按原值保留。每个锚点 occurrence 必须只表达一种可核验状态；若同一 occurrence 同时包含有引用的事实和待核实事项，或 kind=unverified 的 occurrence 带有任何正文引用标记，则该正文无法通过仅修协议元数据来修复，应由后续报告生成拆分 occurrence。只要多个独立行锚点连续跟在同一段落后，就不能猜测锚点和引用的归属；必须按锁定正文可确定的 occurrence 如实声明，服务端会拒绝无法唯一物化的映射。不要漏登记重复出现的待核实 occurrence。不要输出 citations、代码围栏、正文、额外字段、校验结论或虚构信息。服务端仍会执行授权来源、权限、哈希、原文和 Claim 校验；语义支持仍需人工复核。"""


class EvidenceLocation(BaseModel):
    """A source-specific locator for a document chunk or a business record."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    knowledge_base_id: StrictStr | None = Field(default=None, max_length=256)
    document_id: StrictStr | None = Field(default=None, max_length=256)
    chunk_index: int | None = Field(default=None, ge=0)
    record_id: StrictStr | None = Field(default=None, max_length=256)
    call_id: StrictStr | None = Field(default=None, max_length=256)
    field_path: StrictStr | None = Field(default=None, max_length=512)
    locator: StrictStr | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def require_location(self) -> "EvidenceLocation":
        has_kb_chunk = bool(
            self.knowledge_base_id
            and self.document_id
            and self.chunk_index is not None
        )
        if not (
            has_kb_chunk or self.record_id or self.call_id or self.locator
        ):
            raise ValueError("evidence location must identify a source record")
        return self


class EvidenceRecord(BaseModel):
    """Server-normalized evidence copied from an authorized TaskRun snapshot."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    evidence_id: StrictStr = Field(pattern=r"^EV-[0-9a-f]{24}$")
    source_type: StrictStr = Field(min_length=1, max_length=80)
    authorization_subject: StrictStr = Field(min_length=1, max_length=256)
    source_id: StrictStr = Field(min_length=1, max_length=256)
    knowledge_base_name: StrictStr | None = Field(default=None, max_length=256)
    source: StrictStr = Field(min_length=1, max_length=512)
    location: EvidenceLocation
    content_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    source_text: StrictStr = Field(
        min_length=1, max_length=_MAX_SOURCE_TEXT_CHARS
    )
    source_text_truncated: StrictBool
    collected_at: StrictStr | None = Field(default=None, max_length=80)
    source_updated_at: StrictStr | None = Field(default=None, max_length=80)
    evidence_type: StrictStr = Field(min_length=1, max_length=80)


class ClaimOccurrenceRecord(BaseModel):
    """One visible placement of a logical claim in a Markdown document."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    occurrence_id: StrictStr = Field(
        pattern=r"^O-[A-Za-z0-9][A-Za-z0-9_-]{0,44}$"
    )
    text: StrictStr = Field(min_length=1, max_length=2_000)
    citation_ids: list[StrictStr] = Field(max_length=8)


class ClaimRecord(BaseModel):
    """One independently anchored statement that needs evidence or follow-up."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    claim_id: StrictStr = Field(pattern=r"^CL-[A-Za-z0-9][A-Za-z0-9_-]{0,44}$")
    kind: Literal[
        "fact",
        "rule",
        "historical_case",
        "analysis",
        "recommendation",
        "unverified",
        "template_limitation",
    ]
    anchor: StrictStr = Field(pattern=r"^[A-Za-z0-9_-]{1,48}$")
    text: StrictStr = Field(min_length=1, max_length=2_000)
    citation_ids: list[StrictStr] = Field(max_length=8)
    occurrences: list[ClaimOccurrenceRecord] = Field(
        default_factory=list, max_length=64
    )


class _ClaimKindDeclaration(BaseModel):
    """Compact model response used only for protocol-envelope repair."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    claim_id: StrictStr = Field(
        pattern=r"^CL-[A-Za-z0-9][A-Za-z0-9_-]{0,44}$"
    )
    kind: Literal[
        "fact",
        "rule",
        "historical_case",
        "analysis",
        "recommendation",
        "unverified",
        "template_limitation",
    ]


class _TaskCitationDeclaration(BaseModel):
    """One source reference; authorization is checked after materialization."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    citation_id: StrictStr = Field(pattern=r"^C[1-9][0-9]{0,2}$")
    evidence_id: StrictStr = Field(pattern=r"^EV-[0-9a-f]{24}$")
    quote: StrictStr = Field(min_length=1, max_length=_MAX_QUOTE_CHARS)


class CitationBinding(BaseModel):
    """A claim-specific excerpt from the exact source record behind a citation."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    claim_id: StrictStr = Field(pattern=r"^CL-[A-Za-z0-9][A-Za-z0-9_-]{0,44}$")
    occurrence_id: StrictStr | None = Field(
        default=None,
        pattern=r"^O-[A-Za-z0-9][A-Za-z0-9_-]{0,44}$",
    )
    citation_id: StrictStr = Field(pattern=r"^C[1-9][0-9]{0,2}$")
    evidence_id: StrictStr = Field(pattern=r"^EV-[0-9a-f]{24}$")
    quote: StrictStr = Field(min_length=1, max_length=_MAX_QUOTE_CHARS)


class UnverifiedRecord(BaseModel):
    """An explicitly unresolved claim with a stable, independent identifier."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    unverified_id: StrictStr = Field(
        pattern=r"^UV-[A-Za-z0-9][A-Za-z0-9_-]{0,44}$"
    )
    claim_id: StrictStr = Field(pattern=r"^CL-[A-Za-z0-9][A-Za-z0-9_-]{0,44}$")
    occurrence_id: StrictStr | None = Field(
        default=None,
        pattern=r"^O-[A-Za-z0-9][A-Za-z0-9_-]{0,44}$",
    )
    status: Literal[
        "pending_verification",
        "pending_supplement",
        "pending_confirmation",
    ] = "pending_verification"
    reason: StrictStr = Field(min_length=1, max_length=1_000)
    expected_evidence_types: list[StrictStr] = Field(
        min_length=1, max_length=8
    )


class EvidencePolicy(BaseModel):
    """Scenario-owned source rules applied by the generic claim validator."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    policy_id: StrictStr = Field(
        pattern=r"^[a-z][a-z0-9_.-]{1,119}$"
    )
    allowed_evidence_types_by_claim_kind: dict[
        Literal[
            "fact",
            "rule",
            "historical_case",
            "analysis",
            "recommendation",
            "unverified",
            "template_limitation",
        ],
        list[StrictStr],
    ] = Field(default_factory=dict, max_length=7)
    require_citation_claim_kinds: list[
        Literal["fact", "rule", "historical_case", "template_limitation"]
    ] = Field(default_factory=list, max_length=4)
    manual_review_claim_kinds: list[
        Literal[
            "fact",
            "rule",
            "historical_case",
            "analysis",
            "recommendation",
            "unverified",
            "template_limitation",
        ]
    ] = Field(
        default_factory=lambda: [
            "fact",
            "rule",
            "historical_case",
            "analysis",
            "recommendation",
            "template_limitation",
        ],
        max_length=7,
    )

    @model_validator(mode="after")
    def validate_policy_lists(self) -> "EvidencePolicy":
        """Reject ambiguous policy entries before any Run is evaluated."""

        if len(set(self.require_citation_claim_kinds)) != len(
            self.require_citation_claim_kinds
        ):
            raise ValueError("required citation claim kinds must be unique")
        if len(set(self.manual_review_claim_kinds)) != len(
            self.manual_review_claim_kinds
        ):
            raise ValueError("manual review claim kinds must be unique")
        for claim_kind, evidence_types in (
            self.allowed_evidence_types_by_claim_kind.items()
        ):
            if not evidence_types or any(
                not item.strip() or len(item) > 80 for item in evidence_types
            ):
                raise ValueError(
                    f"allowed evidence types are invalid for {claim_kind}"
                )
            if len(set(evidence_types)) != len(evidence_types):
                raise ValueError(
                    f"allowed evidence types repeat for {claim_kind}"
                )
        return self


@dataclass(frozen=True)
class _AnchoredBlock:
    anchor: str
    occurrence_id: str | None
    marker_start: int
    block_start: int
    block_end: int
    text: str


def normalize_evidence_snapshot(
    evidence: list[dict[str, Any]],
    *,
    authorization_subject: str | None,
    collected_at: str | None = None,
) -> list[dict[str, Any]]:
    """Add deterministic, server-derived IDs to existing evidence snapshots.

    Older TaskRuns remain readable: missing IDs are derived when their snapshot
    is loaded, using the enclosing TaskRun owner supplied by the caller.
    """

    normalized: list[dict[str, Any]] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        record = dict(item)
        owner = record.get("authorization_subject") or authorization_subject
        source_type = record.get("source_type") or "knowledge_base_chunk"
        record["authorization_subject"] = owner or ""
        record["source_type"] = source_type
        record["collected_at"] = record.get("collected_at") or collected_at
        record["evidence_id"] = _task_evidence_id(record, owner or "")
        normalized.append(record)
    return normalized


def extract_claim_evidence_inventory(
    document: str,
) -> dict[str, list[Any]] | None:
    """Return stable Claim/Occurrence/UV identities for safe retries.

    The inventory is not an evidence validation result. It only prevents a
    corrective attempt from deleting declarations that were present in the
    rejected output. Malformed or ambiguous protocols are not retryable.
    """

    if len(document.encode("utf-8")) > _MAX_DOCUMENT_BYTES:
        return None
    blocks = list(_CLAIM_BLOCK.finditer(document))
    if len(blocks) != 1:
        return None
    try:
        payload = json.loads(
            blocks[0].group(1), object_pairs_hook=unique_json_object
        )
    except (json.JSONDecodeError, RecursionError, ValueError):
        return None
    if not isinstance(payload, dict) or set(payload) != {
        "claims",
        "bindings",
        "unverified_items",
    }:
        return None
    raw_claims = payload.get("claims")
    raw_bindings = payload.get("bindings")
    raw_unverified = payload.get("unverified_items")
    if not all(
        isinstance(value, list) and len(value) <= _MAX_ITEMS
        for value in (raw_claims, raw_bindings, raw_unverified)
    ):
        return None

    claim_ids: set[str] = set()
    claim_content: dict[str, dict[str, Any]] = {}
    occurrences: set[tuple[str, str]] = set()
    occurrence_content: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in raw_claims:
        try:
            claim = ClaimRecord.model_validate(raw)
        except Exception:  # pylint: disable=broad-except
            return None
        if claim.claim_id in claim_ids:
            return None
        claim_ids.add(claim.claim_id)
        claim_content[claim.claim_id] = {
            "kind": claim.kind,
            "text": claim.text,
            "citation_ids": sorted(claim.citation_ids),
        }
        for occurrence in claim.occurrences:
            key = (claim.claim_id, occurrence.occurrence_id)
            if key in occurrences:
                return None
            occurrences.add(key)
            occurrence_content[key] = {
                "text": occurrence.text,
                "citation_ids": sorted(occurrence.citation_ids),
            }

    unverified_ids: set[str] = set()
    unverified_content: dict[str, dict[str, Any]] = {}
    for raw in raw_unverified:
        try:
            item = UnverifiedRecord.model_validate(raw)
        except Exception:  # pylint: disable=broad-except
            return None
        if item.unverified_id in unverified_ids or item.claim_id not in claim_ids:
            return None
        unverified_ids.add(item.unverified_id)
        unverified_content[item.unverified_id] = {
            "status": item.status,
            "reason": item.reason,
            "expected_evidence_types": list(item.expected_evidence_types),
        }

    return {
        "claim_ids": sorted(claim_ids),
        "claims": claim_content,
        "occurrences": [
            {
                "claim_id": claim_id,
                "occurrence_id": occurrence_id,
                **occurrence_content[(claim_id, occurrence_id)],
            }
            for claim_id, occurrence_id in sorted(occurrences)
        ],
        "unverified_ids": sorted(unverified_ids),
        "unverified_items": unverified_content,
    }


def extract_claim_evidence_protocol_repair_inventory(
    document: str,
) -> dict[str, Any] | None:
    """Capture an unchanged report body for one bounded protocol-only retry.

    This handles an unclosed or malformed current-protocol envelope only. It
    never repairs JSON, infers claim kinds, or supplies source data. The retry
    must preserve the report body byte-for-byte and then pass normal checks.
    """

    if len(document.encode("utf-8")) > _MAX_DOCUMENT_BYTES:
        return None
    if detect_claim_evidence_protocol(document) != "claim_evidence":
        return None
    claim_starts = list(_CLAIM_BLOCK_START.finditer(document))
    if len(claim_starts) != 1 or _CLAIM_BLOCK.search(document):
        return None
    citation_starts = list(_CITATION_BLOCK_START.finditer(document))
    locked_citations: list[dict[str, str]] | None = None
    if citation_starts:
        citation_blocks = list(_CITATIONS_BLOCK.finditer(document))
        if (
            len(citation_starts) != 1
            or len(citation_blocks) != 1
            or citation_blocks[0].start() != citation_starts[0].start()
            or citation_starts[0].start() > claim_starts[0].start()
            or document[
                citation_blocks[0].end() : claim_starts[0].start()
            ].strip()
        ):
            return None
        try:
            citation_payload = json.loads(
                citation_blocks[0].group(1),
                object_pairs_hook=unique_json_object,
            )
        except (json.JSONDecodeError, RecursionError, ValueError):
            return None
        if not isinstance(citation_payload, dict) or set(
            citation_payload
        ) != {"citations"}:
            return None
        raw_citations = citation_payload["citations"]
        if (
            not isinstance(raw_citations, list)
            or not raw_citations
            or len(raw_citations) > _MAX_ITEMS
        ):
            return None
        locked_citations = []
        citation_ids: set[str] = set()
        for raw in raw_citations:
            try:
                citation = _TaskCitationDeclaration.model_validate(raw)
            except Exception:  # pylint: disable=broad-except
                return None
            if citation.citation_id in citation_ids:
                return None
            citation_ids.add(citation.citation_id)
            locked_citations.append(citation.model_dump(mode="json"))
        body = document[: citation_starts[0].start()]
    else:
        body = document[: claim_starts[0].start()]
    if not body.strip() or len(body) > _MAX_PROTOCOL_REPAIR_BODY_CHARS:
        return None

    anchors = [
        (match.group(1), match.group(2))
        for match in _CLAIM_ANCHOR.finditer(body)
    ]
    if not anchors or any(occurrence_id is None for _, occurrence_id in anchors):
        return None
    if len(anchors) != len(set(anchors)):
        return None
    body_citation_ids = set(extract_task_citation_ids(body))
    if locked_citations is not None and body_citation_ids != {
        item["citation_id"] for item in locked_citations
    }:
        return None
    newline = "\r\n" if "\r\n" in body else "\n"
    separator = (
        ""
        if body.endswith(newline * 2)
        else newline if body.endswith(newline) else newline * 2
    )

    inventory = {
        "mode": (
            "protocol_envelope_with_locked_citations"
            if locked_citations is not None
            else "protocol_envelope_only"
        ),
        "body": body,
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "separator": separator,
        "anchor_occurrences": [list(item) for item in anchors],
        "citation_ids": extract_task_citation_ids(body),
    }
    if locked_citations is not None:
        inventory["locked_citations"] = locked_citations
    return inventory


def claim_evidence_protocol_repair_body_matches(
    document: str,
    repair_inventory: dict[str, Any],
) -> bool:
    """Check that a protocol retry kept the complete report body unchanged."""

    starts = list(_CLAIM_BLOCK_START.finditer(document))
    if len(starts) != 1 or detect_claim_evidence_protocol(document) != "claim_evidence":
        return False
    body = repair_inventory.get("body")
    expected_hash = repair_inventory.get("body_sha256")
    separator = repair_inventory.get("separator")
    if (
        not isinstance(body, str)
        or not isinstance(expected_hash, str)
        or not isinstance(separator, str)
        or document[: len(body)] != body
        or document[len(body) : starts[0].start()] != separator
    ):
        return False
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != expected_hash:
        return False
    expected_anchors = repair_inventory.get("anchor_occurrences")
    actual_anchors = [
        [match.group(1), match.group(2)]
        for match in _CLAIM_ANCHOR.finditer(body)
    ]
    return (
        actual_anchors == expected_anchors
        and extract_task_citation_ids(body)
        == repair_inventory.get("citation_ids")
        and _locked_citations_match(document, repair_inventory)
    )


def _locked_citations_match(
    document: str,
    repair_inventory: dict[str, Any],
) -> bool:
    """Ensure a repair retained the original citation declarations unchanged."""

    if "locked_citations" not in repair_inventory:
        return True
    claim_blocks = list(_CLAIM_BLOCK.finditer(document))
    citation_blocks = list(_CITATIONS_BLOCK.finditer(document))
    citation_starts = list(_CITATION_BLOCK_START.finditer(document))
    if (
        len(claim_blocks) != 1
        or len(citation_blocks) != 1
        or len(citation_starts) != 1
        or citation_starts[0].start() <= claim_blocks[0].start()
        or document[citation_blocks[0].end() :].strip()
    ):
        return False
    try:
        payload = json.loads(
            citation_blocks[0].group(1),
            object_pairs_hook=unique_json_object,
        )
    except (json.JSONDecodeError, RecursionError, ValueError):
        return False
    if not isinstance(payload, dict) or set(payload) != {"citations"}:
        return False
    if not isinstance(payload["citations"], list):
        return False
    try:
        actual = [
            _TaskCitationDeclaration.model_validate(item).model_dump(
                mode="json"
            )
            for item in payload["citations"]
        ]
    except Exception:  # pylint: disable=broad-except
        return False
    expected = repair_inventory.get("locked_citations")
    return actual == expected


def materialize_task_claim_evidence_protocol_repair(
    body: str,
    compact_payload: str,
    repair_inventory: dict[str, Any] | None = None,
    *,
    derive_locked_bindings: bool = False,
) -> tuple[str, list[str]]:
    """Build the verbose protocol from claim kinds and occurrence bindings.

    Occurrence text, anchors, and citation markers are read from the preserved
    report body. Explicit bindings are preferred. A targeted local content
    split may derive a missing binding only from one uniquely matching,
    occurrence-specific substring of an already locked citation quote; the
    completed document still passes the ordinary source, authorization,
    quote, and claim validators.
    """

    if len(body.encode("utf-8")) > _MAX_PROTOCOL_REPAIR_BODY_CHARS * 4:
        return body, []
    if _CLAIM_BLOCK_START.search(body) or _CITATION_BLOCK_START.search(body):
        return body, []
    try:
        payload = json.loads(
            compact_payload, object_pairs_hook=unique_json_object
        )
    except (json.JSONDecodeError, RecursionError, ValueError):
        return body, []
    locked_citation_mode = (
        isinstance(repair_inventory, dict)
        and "locked_citations" in repair_inventory
    )
    expected_keys = (
        {"claims", "bindings", "unverified_items"}
        if locked_citation_mode
        else {"claims", "citations", "bindings", "unverified_items"}
    )
    compact_locked_keys = {"claims", "unverified_items"}
    accepted_key_sets = (
        {frozenset(expected_keys), frozenset(compact_locked_keys)}
        if locked_citation_mode and derive_locked_bindings
        else {frozenset(expected_keys)}
    )
    if not isinstance(payload, dict) or frozenset(payload) not in accepted_key_sets:
        return body, []

    raw_claims = payload["claims"]
    raw_citations = payload.get("citations", [])
    raw_bindings = payload.get("bindings", [])
    raw_unverified = payload["unverified_items"]
    if not all(
        isinstance(items, list) and len(items) <= _MAX_ITEMS
        for items in (raw_claims, raw_citations, raw_bindings, raw_unverified)
    ):
        return body, []

    claim_kinds: dict[str, str] = {}
    for raw in raw_claims:
        try:
            declaration = _ClaimKindDeclaration.model_validate(raw)
        except Exception:  # pylint: disable=broad-except
            return body, []
        if declaration.claim_id in claim_kinds:
            return body, []
        claim_kinds[declaration.claim_id] = declaration.kind

    citation_declarations: dict[str, _TaskCitationDeclaration] = {}
    citations_to_validate = (
        repair_inventory.get("locked_citations")
        if locked_citation_mode and isinstance(repair_inventory, dict)
        else raw_citations
    )
    if not isinstance(citations_to_validate, list):
        return body, []
    for raw in citations_to_validate:
        try:
            declaration = _TaskCitationDeclaration.model_validate(raw)
        except Exception:  # pylint: disable=broad-except
            return body, []
        if declaration.citation_id in citation_declarations:
            return body, []
        citation_declarations[declaration.citation_id] = declaration

    anchors = _anchored_blocks(body)
    if not anchors or any(not item.occurrence_id for item in anchors):
        return body, []
    grouped: dict[str, list[dict[str, Any]]] = {}
    anchor_keys: set[tuple[str, str]] = set()
    body_citation_ids: set[str] = set()
    for item in anchors:
        key = (item.anchor, str(item.occurrence_id))
        if key in anchor_keys or item.anchor not in claim_kinds:
            return body, []
        anchor_keys.add(key)
        citation_ids = extract_task_citation_ids(item.text)
        if len(citation_ids) != len(set(citation_ids)):
            return body, []
        body_citation_ids.update(citation_ids)
        grouped.setdefault(item.anchor, []).append(
            {
                "occurrence_id": str(item.occurrence_id),
                "text": item.text,
                "citation_ids": citation_ids,
            }
        )
    if set(grouped) != set(claim_kinds):
        return body, []
    if body_citation_ids != set(citation_declarations):
        return body, []

    expected_binding_keys = {
        (item.anchor, str(item.occurrence_id), citation_id)
        for item in anchors
        if item.occurrence_id
        for citation_id in extract_task_citation_ids(item.text)
    }
    if (
        derive_locked_bindings
        and locked_citation_mode
        and "bindings" not in payload
    ):
        occurrence_by_key = {
            (item.anchor, str(item.occurrence_id)): item.text
            for item in anchors
            if item.occurrence_id
        }
        derived_bindings: list[dict[str, str]] = []
        for claim_id, occurrence_id, citation_id in sorted(
            expected_binding_keys
        ):
            citation = citation_declarations.get(citation_id)
            occurrence_text = occurrence_by_key.get(
                (claim_id, occurrence_id)
            )
            quote = (
                _derive_occurrence_quote(
                    occurrence_text,
                    citation.quote,
                )
                if citation is not None and occurrence_text is not None
                else None
            )
            if citation is None or quote is None:
                return body, []
            derived_bindings.append(
                {
                    "claim_id": claim_id,
                    "occurrence_id": occurrence_id,
                    "citation_id": citation_id,
                    "evidence_id": citation.evidence_id,
                    "quote": quote,
                }
            )
        raw_bindings = derived_bindings
    bindings: list[dict[str, str]] = []
    binding_keys: set[tuple[str, str, str]] = set()
    for raw in raw_bindings:
        try:
            binding = CitationBinding.model_validate(raw)
        except Exception:  # pylint: disable=broad-except
            return body, []
        if binding.occurrence_id is None:
            return body, []
        key = (
            binding.claim_id,
            binding.occurrence_id,
            binding.citation_id,
        )
        citation = citation_declarations.get(binding.citation_id)
        if (
            key not in expected_binding_keys
            or key in binding_keys
            or citation is None
            or binding.evidence_id != citation.evidence_id
        ):
            return body, []
        binding_keys.add(key)
        bindings.append(binding.model_dump(mode="json"))
    if binding_keys != expected_binding_keys:
        return body, []

    claims: list[dict[str, Any]] = []
    expected_unverified: set[tuple[str, str]] = set()
    for claim_id, occurrences in grouped.items():
        kind = claim_kinds[claim_id]
        citation_ids = list(
            dict.fromkeys(
                citation_id
                for occurrence in occurrences
                for citation_id in occurrence["citation_ids"]
            )
        )
        if kind == "unverified":
            if citation_ids:
                return body, []
            expected_unverified.update(
                (claim_id, occurrence["occurrence_id"])
                for occurrence in occurrences
            )
        claim = {
            "claim_id": claim_id,
            "kind": kind,
            "anchor": claim_id,
            "text": occurrences[0]["text"],
            "citation_ids": citation_ids,
            "occurrences": occurrences,
        }
        claims.append(claim)

    unverified_items: list[dict[str, Any]] = []
    supplied_unverified: set[tuple[str, str]] = set()
    unverified_ids: set[str] = set()
    for raw in raw_unverified:
        try:
            item = UnverifiedRecord.model_validate(raw)
        except Exception:  # pylint: disable=broad-except
            return body, []
        if (
            item.occurrence_id is None
            or item.unverified_id in unverified_ids
            or item.claim_id not in claim_kinds
            or claim_kinds[item.claim_id] != "unverified"
        ):
            return body, []
        key = (item.claim_id, item.occurrence_id)
        if key in supplied_unverified:
            return body, []
        supplied_unverified.add(key)
        unverified_ids.add(item.unverified_id)
        unverified_items.append(item.model_dump(mode="json"))
    if supplied_unverified != expected_unverified:
        return body, []

    citations = [
        declaration.model_dump(mode="json")
        for declaration in sorted(
            citation_declarations.values(),
            key=lambda item: int(item.citation_id[1:]),
        )
    ]
    protocol = {
        "claims": claims,
        "bindings": bindings,
        "unverified_items": unverified_items,
    }
    newline = "\r\n" if "\r\n" in body else "\n"
    separator = (
        ""
        if body.endswith(newline * 2)
        else newline if body.endswith(newline) else newline * 2
    )
    finalized = (
        body + separator
        + "```task-claim-evidence\n"
        + json.dumps(
            protocol,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n```\n\n```task-citations\n"
        + json.dumps(
            {"citations": citations},
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n```"
    )
    if len(finalized.encode("utf-8")) > _MAX_DOCUMENT_BYTES:
        return body, []
    return finalized, ["materialized_claim_evidence_from_compact_repair"]


def diagnose_task_claim_evidence_protocol_materialization(
    body: str,
    compact_payload: str,
    repair_inventory: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Explain why a compact protocol response cannot be safely expanded.

    This is diagnostic only. It never changes the body, assigns evidence, or
    relaxes the validators used after materialization.
    """

    diagnostics: list[dict[str, Any]] = []
    try:
        payload = json.loads(
            compact_payload, object_pairs_hook=unique_json_object
        )
    except (json.JSONDecodeError, RecursionError, ValueError):
        return [
            {
                "error_code": "repair_payload_invalid",
                "message": "协议修复响应不是合法且无重复键的 JSON。",
            }
        ]
    locked_citation_mode = (
        isinstance(repair_inventory, dict)
        and "locked_citations" in repair_inventory
    )
    expected_keys = (
        {"claims", "bindings", "unverified_items"}
        if locked_citation_mode
        else {"claims", "citations", "bindings", "unverified_items"}
    )
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        return [
            {
                "error_code": "repair_payload_shape_invalid",
                "message": "精简修复响应字段与当前 Task 协议不符，未生成完整协议。",
            }
        ]
    raw_claims = payload.get("claims")
    raw_bindings = payload.get("bindings")
    raw_citations = (
        repair_inventory.get("locked_citations", [])
        if locked_citation_mode and isinstance(repair_inventory, dict)
        else payload.get("citations")
    )
    raw_unverified = payload.get("unverified_items")
    if (
        not isinstance(raw_claims, list)
        or not isinstance(raw_bindings, list)
        or not isinstance(raw_citations, list)
        or not isinstance(raw_unverified, list)
        or len(raw_claims) > _MAX_ITEMS
        or len(raw_bindings) > _MAX_ITEMS
        or len(raw_citations) > _MAX_ITEMS
        or len(raw_unverified) > _MAX_ITEMS
    ):
        return [
            {
                "error_code": "repair_payload_items_invalid",
                "message": "精简修复响应中的 claims、bindings、citations 或 unverified_items 不是有效数组。",
            }
        ]

    claim_kinds: dict[str, str] = {}
    for raw in raw_claims:
        try:
            declaration = _ClaimKindDeclaration.model_validate(raw)
        except Exception:  # pylint: disable=broad-except
            return [
                {
                    "error_code": "repair_claim_declaration_invalid",
                    "message": "精简修复响应含无效 Claim 声明。",
                }
            ]
        if declaration.claim_id in claim_kinds:
            return [
                {
                    "error_code": "repair_claim_declaration_duplicate",
                    "claim_id": declaration.claim_id,
                    "message": "精简修复响应重复声明同一 Claim。",
                }
            ]
        claim_kinds[declaration.claim_id] = declaration.kind

    detached_groups = _detached_anchor_groups(body)
    if detached_groups:
        diagnostics.append(
            {
                "error_code": "ambiguous_detached_anchor_group",
                "anchor_groups": detached_groups,
                "message": (
                    "多个独立行的 Claim 锚点连续跟在同一正文段落后，"
                    "无法唯一分配正文文本和引用编号；请拆成独立段落或表格行，"
                    "并将每个锚点内联在它对应的内容行末。"
                ),
            }
        )

    anchors = _anchored_blocks(body)
    citation_map: dict[str, _TaskCitationDeclaration] = {}
    for raw in raw_citations:
        try:
            citation = _TaskCitationDeclaration.model_validate(raw)
        except Exception:  # pylint: disable=broad-except
            diagnostics.append(
                {
                    "error_code": "repair_citation_declaration_invalid",
                    "message": "引用声明不是有效的来源记录。",
                }
            )
            continue
        if citation.citation_id in citation_map:
            diagnostics.append(
                {
                    "error_code": "repair_citation_declaration_duplicate",
                    "message": "协议修复重复声明同一引用编号。",
                }
            )
            continue
        citation_map[citation.citation_id] = citation

    expected_binding_keys = {
        (item.anchor, str(item.occurrence_id), citation_id)
        for item in anchors
        if item.occurrence_id
        for citation_id in extract_task_citation_ids(item.text)
    }
    supplied_binding_keys: set[tuple[str, str, str]] = set()
    for raw in raw_bindings:
        try:
            binding = CitationBinding.model_validate(raw)
        except Exception:  # pylint: disable=broad-except
            diagnostics.append(
                {
                    "error_code": "repair_binding_invalid",
                    "message": "协议修复缺少有效的逐 occurrence 来源绑定。",
                }
            )
            continue
        if binding.occurrence_id is None:
            diagnostics.append(
                {
                    "error_code": "repair_binding_occurrence_missing",
                    "claim_id": binding.claim_id,
                    "message": "每条来源绑定必须指定 occurrence_id。",
                }
            )
            continue
        key = (
            binding.claim_id,
            binding.occurrence_id,
            binding.citation_id,
        )
        citation = citation_map.get(binding.citation_id)
        if key not in expected_binding_keys:
            diagnostics.append(
                {
                    "error_code": "repair_binding_not_declared",
                    "claim_id": binding.claim_id,
                    "occurrence_id": binding.occurrence_id,
                    "message": "绑定未对应正文声明的 occurrence/citation 组合。",
                }
            )
        elif key in supplied_binding_keys:
            diagnostics.append(
                {
                    "error_code": "repair_binding_duplicate",
                    "claim_id": binding.claim_id,
                    "occurrence_id": binding.occurrence_id,
                    "message": "同一 occurrence/citation 组合重复绑定。",
                }
            )
        elif citation is None or binding.evidence_id != citation.evidence_id:
            diagnostics.append(
                {
                    "error_code": "repair_binding_source_mismatch",
                    "claim_id": binding.claim_id,
                    "occurrence_id": binding.occurrence_id,
                    "message": "绑定 evidence_id 与对应 citation 的服务端来源标识不一致。",
                }
            )
        supplied_binding_keys.add(key)
    missing_binding_keys = sorted(expected_binding_keys - supplied_binding_keys)
    if missing_binding_keys:
        diagnostics.append(
            {
                "error_code": "repair_occurrence_bindings_missing",
                "occurrences": [
                    f"{claim_id}:{occurrence_id}:{citation_id}"
                    for claim_id, occurrence_id, citation_id
                    in missing_binding_keys
                ],
                "message": "协议修复必须逐一提供正文 occurrence/citation 绑定，不能从单条引用摘要自动复制。",
            }
        )
    anchor_pairs = {
        (item.anchor, str(item.occurrence_id))
        for item in anchors
        if item.occurrence_id
    }
    declared_claim_ids = {item.anchor for item in anchors}
    if declared_claim_ids != set(claim_kinds):
        diagnostics.append(
            {
                "error_code": "repair_claim_anchor_set_mismatch",
                "missing_claim_ids": sorted(declared_claim_ids - set(claim_kinds)),
                "unanchored_claim_ids": sorted(set(claim_kinds) - declared_claim_ids),
                "message": "Claim 声明与正文中的稳定锚点集合不一致。",
            }
        )

    expected_unverified = {
        pair
        for pair in anchor_pairs
        if claim_kinds.get(pair[0]) == "unverified"
    }
    supplied_unverified: set[tuple[str, str]] = set()
    for raw in raw_unverified:
        if not isinstance(raw, dict):
            continue
        claim_id = raw.get("claim_id")
        occurrence_id = raw.get("occurrence_id")
        if isinstance(claim_id, str) and isinstance(occurrence_id, str):
            supplied_unverified.add((claim_id, occurrence_id))
    missing_unverified = sorted(expected_unverified - supplied_unverified)
    extra_unverified = sorted(supplied_unverified - expected_unverified)
    if missing_unverified or extra_unverified:
        diagnostics.append(
            {
                "error_code": "repair_unverified_occurrence_set_mismatch",
                "missing_occurrences": [
                    f"{claim_id}:{occurrence_id}"
                    for claim_id, occurrence_id in missing_unverified
                ],
                "unmatched_occurrences": [
                    f"{claim_id}:{occurrence_id}"
                    for claim_id, occurrence_id in extra_unverified
                ],
                "message": (
                    "待核实登记必须逐个对应正文中 kind=unverified 的 Claim occurrence；"
                    "当前登记存在遗漏或不匹配。"
                ),
            }
        )

    cited_occurrences = {
        (item.anchor, str(item.occurrence_id)): extract_task_citation_ids(item.text)
        for item in anchors
        if item.occurrence_id
    }
    unverified_with_citations = {
        key: citation_ids
        for key, citation_ids in cited_occurrences.items()
        if claim_kinds.get(key[0]) == "unverified" and citation_ids
    }
    if unverified_with_citations:
        diagnostics.append(
            {
                "error_code": "unverified_occurrence_contains_citation",
                "occurrences": [
                    {
                        "claim_id": claim_id,
                        "occurrence_id": occurrence_id,
                        "citation_ids": citation_ids,
                    }
                    for (claim_id, occurrence_id), citation_ids
                    in sorted(unverified_with_citations.items())
                ],
                "message": (
                    "部分 kind=unverified 的 occurrence 含正文引用标记；"
                    "有来源陈述与待核实内容必须拆成独立 Claim。"
                ),
            }
        )

    occurrence_citations = {
        citation_id
        for values in cited_occurrences.values()
        for citation_id in values
    }
    body_citations = set(extract_task_citation_ids(body))
    if body_citations - occurrence_citations:
        diagnostics.append(
            {
                "error_code": "body_citations_not_bound_to_occurrences",
                "citation_ids": sorted(body_citations - occurrence_citations),
                "message": "正文中的部分引用编号未能唯一映射到 Claim occurrence。",
            }
        )
    if not diagnostics:
        diagnostics.append(
            {
                "error_code": "protocol_materialization_refused_unclassified",
                "message": "协议无法确定性物化；未完成来源或引用核验，需检查完整运行诊断。",
            }
        )
    return diagnostics


def wrap_claim_evidence_repair_payload_for_validation(
    body: str,
    raw_payload: str,
    repair_inventory: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    """Wrap an unmaterializable repair response without changing locked text.

    The wrapper is not a successful repair. It only preserves the exact body
    boundary (and any server-locked citation block) so the normal validators
    can report the payload's actual protocol errors instead of misreporting a
    changed report body.
    """

    if (
        not isinstance(repair_inventory, dict)
        or repair_inventory.get("body") != body
        or not isinstance(raw_payload, str)
        or len(raw_payload) > _MAX_DOCUMENT_BYTES
    ):
        return body, []
    separator = repair_inventory.get("separator")
    if not isinstance(separator, str):
        return body, []
    newline = "\r\n" if "\r\n" in body else "\n"
    expected_separator = (
        ""
        if body.endswith(newline * 2)
        else newline if body.endswith(newline) else newline * 2
    )
    if separator != expected_separator:
        return body, []

    wrapped = (
        body
        + separator
        + "```task-claim-evidence\n"
        + raw_payload
        + "\n```"
    )
    locked_citations = repair_inventory.get("locked_citations")
    if locked_citations is not None:
        if not isinstance(locked_citations, list):
            return body, []
        wrapped += (
            "\n\n```task-citations\n"
            + json.dumps(
                {"citations": locked_citations},
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n```"
        )
    if len(wrapped.encode("utf-8")) > _MAX_DOCUMENT_BYTES:
        return body, []
    if not claim_evidence_protocol_repair_body_matches(
        wrapped, repair_inventory
    ):
        return body, []
    return wrapped, ["wrapped_unmaterialized_payload_for_full_validation"]


def _detached_anchor_groups(body: str) -> list[dict[str, Any]]:
    """Find consecutive standalone anchor comments after one body paragraph."""

    lines = body.splitlines()
    marker_lines: dict[int, list[tuple[str, str | None]]] = {}
    for index, line in enumerate(lines):
        matches = list(_CLAIM_ANCHOR.finditer(line))
        if not matches or _CLAIM_ANCHOR.sub("", line).strip():
            continue
        marker_lines[index] = [
            (match.group(1), match.group(2)) for match in matches
        ]

    groups: list[dict[str, Any]] = []
    indexes = sorted(marker_lines)
    cursor = 0
    while cursor < len(indexes):
        run = [indexes[cursor]]
        cursor += 1
        while cursor < len(indexes) and indexes[cursor] == run[-1] + 1:
            run.append(indexes[cursor])
            cursor += 1
        anchors = [
            item for index in run for item in marker_lines[index]
        ]
        if len(anchors) < 2:
            continue
        preceding = lines[run[0] - 1].strip() if run[0] > 0 else ""
        if not preceding or preceding.startswith("#"):
            continue
        groups.append(
            {
                "line_start": run[0] + 1,
                "line_end": run[-1] + 1,
                "anchors": [
                    {
                        "claim_id": claim_id,
                        "occurrence_id": occurrence_id,
                    }
                    for claim_id, occurrence_id in anchors
                ],
                "preceding_citation_ids": extract_task_citation_ids(preceding),
            }
        )
    return groups


def _parse_content_repair_payload(
    document: str,
    *,
    allow_locked_citations: bool,
) -> tuple[str, dict[str, Any]] | None:
    if len(document.encode("utf-8")) > _MAX_DOCUMENT_BYTES:
        return None
    claim_starts = list(_CLAIM_BLOCK_START.finditer(document))
    claim_blocks = list(_CLAIM_BLOCK.finditer(document))
    citation_starts = list(_CITATION_BLOCK_START.finditer(document))
    citation_blocks = list(_CITATIONS_BLOCK.finditer(document))
    if (
        len(claim_starts) != 1
        or len(claim_blocks) != 1
        or claim_starts[0].start() != claim_blocks[0].start()
        or len(citation_starts) != len(citation_blocks)
        or len(citation_blocks) != (1 if allow_locked_citations else 0)
    ):
        return None
    claim_block = claim_blocks[0]
    body = document[: claim_block.start()]
    if not body.strip() or len(body) > _MAX_PROTOCOL_REPAIR_BODY_CHARS:
        return None
    if allow_locked_citations:
        citation_block = citation_blocks[0]
        if (
            citation_starts[0].start() != citation_block.start()
            or citation_block.start() <= claim_block.start()
            or document[claim_block.end() : citation_block.start()].strip()
            or document[citation_block.end() :].strip()
        ):
            return None
    elif document[claim_block.end() :].strip():
        return None
    try:
        payload = json.loads(
            claim_block.group(1), object_pairs_hook=unique_json_object
        )
    except (json.JSONDecodeError, RecursionError, ValueError):
        return None
    compact_keys = {"claims", "unverified_items"}
    full_keys = {"claims", "bindings", "unverified_items"}
    accepted_keys = (
        {frozenset(compact_keys), frozenset(full_keys)}
        if allow_locked_citations
        else {frozenset(compact_keys)}
    )
    if not isinstance(payload, dict) or frozenset(payload) not in accepted_keys:
        return None
    list_fields = ["claims", "unverified_items"]
    if "bindings" in payload:
        list_fields.append("bindings")
    if not all(
        isinstance(payload.get(key), list)
        and len(payload[key]) <= _MAX_ITEMS
        for key in list_fields
    ):
        return None
    if not body.strip() or not extract_task_citation_ids(body):
        return None
    return body, payload


def _protocol_separator(body: str) -> str:
    newline = "\r\n" if "\r\n" in body else "\n"
    return (
        ""
        if body.endswith(newline * 2)
        else newline if body.endswith(newline) else newline * 2
    )


def _normalize_repair_content(value: str) -> str:
    value = _CLAIM_ANCHOR.sub("", value)
    value = re.sub(r"\[C[1-9][0-9]{0,2}\]", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"(?m)^\s*(?:[-*+]\s+|\d+[.、]\s*)", "", value)
    return re.sub(
        r"[\s|*_`~，。；：:、\-—()（）【】\[\]<>]+", "", value
    )


def _quote_match_text(value: str) -> tuple[str, list[int]]:
    ignored = set(" \t\r\n|*_`~，。；：:、-—()（）【】[]<>")
    chars: list[str] = []
    offsets: list[int] = []
    for offset, char in enumerate(value):
        if char.isspace() or char in ignored:
            continue
        chars.append(char)
        offsets.append(offset)
    return "".join(chars), offsets


def _derive_occurrence_quote(
    occurrence_text: str,
    locked_quote: str,
) -> str | None:
    """Select a unique, narrow, exact substring shared by occurrence and quote."""

    occurrence = _CLAIM_ANCHOR.sub("", occurrence_text)
    occurrence = re.sub(r"\[C[1-9][0-9]{0,2}\]", "", occurrence)
    occurrence = re.sub(r"<[^>]+>", "", occurrence)
    occurrence_normalized, occurrence_offsets = _quote_match_text(occurrence)
    quote_normalized, quote_offsets = _quote_match_text(locked_quote)
    if not occurrence_normalized or not quote_normalized:
        return None

    previous = [0] * (len(quote_normalized) + 1)
    best_length = 0
    best_matches: set[tuple[int, int, int]] = set()
    for occurrence_index, occurrence_char in enumerate(
        occurrence_normalized, start=1
    ):
        current = [0]
        for quote_index, quote_char in enumerate(quote_normalized, start=1):
            length = (
                previous[quote_index - 1] + 1
                if occurrence_char == quote_char
                else 0
            )
            current.append(length)
            if length > best_length:
                best_length = length
                best_matches = {
                    (
                        occurrence_index - length,
                        quote_index - length,
                        length,
                    )
                }
            elif length == best_length and length:
                best_matches.add(
                    (
                        occurrence_index - length,
                        quote_index - length,
                        length,
                    )
                )
        previous = current

    # Short common words are too weak to establish an occurrence-specific
    # mapping. More substantial matches still require normal source checks.
    if best_length < 6 or len(best_matches) != 1:
        return None
    _, quote_start, length = next(iter(best_matches))
    raw_start = quote_offsets[quote_start]
    raw_end = quote_offsets[quote_start + length - 1] + 1
    exact_quote = locked_quote[raw_start:raw_end]
    if not exact_quote.strip():
        return None
    return exact_quote


def _validate_locked_content_repair_body(
    body: str,
    payload: dict[str, Any],
    repair_inventory: dict[str, Any],
) -> dict[str, Any]:
    original = repair_inventory.get("protocol_repair")
    conflict = repair_inventory.get("conflict")
    baseline_claims = repair_inventory.get("baseline_claims")
    baseline_unverified = repair_inventory.get("baseline_unverified_items")
    if (
        not isinstance(original, dict)
        or not isinstance(original.get("body"), str)
        or not isinstance(conflict, dict)
        or not isinstance(baseline_claims, list)
        or not isinstance(baseline_unverified, list)
    ):
        return {"passed": False, "reason": "repair_inventory_invalid"}

    old_body = original["body"]
    old_lines = old_body.splitlines(keepends=True)
    new_lines = body.splitlines(keepends=True)
    target = (conflict.get("claim_id"), conflict.get("occurrence_id"))
    target_markers = [
        match
        for match in _CLAIM_ANCHOR.finditer(old_body)
        if (match.group(1), match.group(2)) == target
    ]
    if len(target_markers) != 1:
        return {"passed": False, "reason": "target_occurrence_ambiguous"}
    target_marker = target_markers[0]
    target_line_index = old_body[: target_marker.start()].count("\n")
    if target_line_index >= len(old_lines):
        return {"passed": False, "reason": "target_line_missing"}
    old_target_line = old_lines[target_line_index]
    if len(list(_CLAIM_ANCHOR.finditer(old_target_line))) != 1:
        return {"passed": False, "reason": "target_line_not_isolated"}

    matcher = SequenceMatcher(
        a=old_lines, b=new_lines, autojunk=False
    )
    changed_new_lines: set[int] = set()
    target_line_replaced = False
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            if old_start not in {target_line_index, target_line_index + 1}:
                return {"passed": False, "reason": "unrelated_body_change"}
        elif (
            old_start != target_line_index
            or old_end != target_line_index + 1
        ):
            return {"passed": False, "reason": "unrelated_body_change"}
        else:
            target_line_replaced = True
        changed_new_lines.update(range(new_start, new_end))
    if not target_line_replaced:
        return {"passed": False, "reason": "target_line_not_revised"}

    old_pairs = [
        (match.group(1), match.group(2))
        for match in _CLAIM_ANCHOR.finditer(old_body)
    ]
    new_markers = list(_CLAIM_ANCHOR.finditer(body))
    new_pairs = [
        (match.group(1), match.group(2)) for match in new_markers
    ]
    if (
        len(old_pairs) != len(set(old_pairs))
        or len(new_pairs) != len(set(new_pairs))
        or new_pairs.count(target) != 1
    ):
        return {"passed": False, "reason": "anchor_identity_changed"}
    old_non_target = [item for item in old_pairs if item != target]
    new_only_pairs = [item for item in new_pairs if item not in old_pairs]
    new_existing_order = [item for item in new_pairs if item in old_pairs]
    if (
        new_existing_order != old_pairs
        or len(new_only_pairs) != 1
        or abs(new_pairs.index(new_only_pairs[0]) - new_pairs.index(target))
        != 1
    ):
        return {"passed": False, "reason": "target_split_not_local"}

    def marker_line_index(text: str, marker: re.Match[str]) -> int:
        return text[: marker.start()].count("\n")

    target_new_markers = [
        match
        for match in new_markers
        if (match.group(1), match.group(2)) == target
    ]
    added_markers = [
        match
        for match in new_markers
        if (match.group(1), match.group(2)) == new_only_pairs[0]
    ]
    if len(target_new_markers) != 1 or len(added_markers) != 1:
        return {"passed": False, "reason": "target_split_ambiguous"}
    target_new_line = marker_line_index(body, target_new_markers[0])
    added_new_line = marker_line_index(body, added_markers[0])
    if (
        target_new_line == added_new_line
        or target_new_line not in changed_new_lines
        or added_new_line not in changed_new_lines
        or len(list(_CLAIM_ANCHOR.finditer(new_lines[target_new_line]))) != 1
        or len(list(_CLAIM_ANCHOR.finditer(new_lines[added_new_line]))) != 1
    ):
        return {"passed": False, "reason": "target_split_not_separate"}

    target_text = "".join(
        new_lines[min(target_new_line, added_new_line) : max(
            target_new_line, added_new_line
        ) + 1]
    )
    old_content = _normalize_repair_content(old_target_line)
    if (
        not old_content
        or old_content != _normalize_repair_content(target_text)
    ):
        return {"passed": False, "reason": "target_content_not_preserved"}

    old_anchor_blocks = _anchored_blocks(old_body)
    new_anchor_blocks = _anchored_blocks(body)
    old_block_by_key = {
        (item.anchor, item.occurrence_id): item for item in old_anchor_blocks
    }
    new_block_by_key = {
        (item.anchor, item.occurrence_id): item for item in new_anchor_blocks
    }
    target_block = old_block_by_key.get(target)
    target_new_block = new_block_by_key.get(target)
    added_block = new_block_by_key.get(new_only_pairs[0])
    if (
        target_block is None
        or target_new_block is None
        or added_block is None
        or extract_task_citation_ids(target_new_block.text)
        != conflict.get("citation_ids")
        or extract_task_citation_ids(added_block.text)
    ):
        return {"passed": False, "reason": "target_citation_binding_changed"}
    for key in old_non_target:
        old_marker = next(
            (
                match
                for match in _CLAIM_ANCHOR.finditer(old_body)
                if (match.group(1), match.group(2)) == key
            ),
            None,
        )
        new_marker = next(
            (
                match
                for match in new_markers
                if (match.group(1), match.group(2)) == key
            ),
            None,
        )
        if old_marker is None or new_marker is None:
            return {"passed": False, "reason": "unrelated_occurrence_changed"}
        old_line_index = old_body[: old_marker.start()].count("\n")
        new_line_index = body[: new_marker.start()].count("\n")
        if old_lines[old_line_index] != new_lines[new_line_index]:
            return {"passed": False, "reason": "unrelated_occurrence_changed"}

    if extract_task_citation_ids(body) != original.get("citation_ids"):
        return {"passed": False, "reason": "citation_ids_changed"}

    old_kinds: dict[str, str] = {}
    new_kinds: dict[str, str] = {}
    for raw, target_map in (
        (baseline_claims, old_kinds),
        (payload.get("claims", []), new_kinds),
    ):
        if not isinstance(raw, list) or len(raw) > _MAX_ITEMS:
            return {"passed": False, "reason": "claim_declarations_invalid"}
        for item in raw:
            try:
                declaration = _ClaimKindDeclaration.model_validate(item)
            except Exception:  # pylint: disable=broad-except
                return {"passed": False, "reason": "claim_declarations_invalid"}
            if declaration.claim_id in target_map:
                return {"passed": False, "reason": "duplicate_claim_declaration"}
            target_map[declaration.claim_id] = declaration.kind
    old_ids = set(old_kinds)
    new_ids = set(new_kinds)
    added_claim_ids = new_ids - old_ids
    if (
        target[0] not in old_kinds
        or old_kinds[target[0]] != "unverified"
        or new_ids != old_ids | {new_only_pairs[0][0]}
        or {claim_id for claim_id, _ in new_pairs} != new_ids
        or new_only_pairs[0][0] in old_ids
        or new_kinds.get(target[0]) == "unverified"
        or new_kinds.get(new_only_pairs[0][0]) != "unverified"
        or any(
            new_kinds.get(claim_id) != kind
            for claim_id, kind in old_kinds.items()
            if claim_id != target[0]
        )
        or len(added_claim_ids) != 1
    ):
        return {"passed": False, "reason": "claim_declarations_changed"}

    old_unverified: dict[str, dict[str, Any]] = {}
    new_unverified: dict[str, dict[str, Any]] = {}
    for raw, target_map in (
        (baseline_unverified, old_unverified),
        (payload.get("unverified_items", []), new_unverified),
    ):
        if not isinstance(raw, list) or len(raw) > _MAX_ITEMS:
            return {"passed": False, "reason": "unverified_records_invalid"}
        for record in raw:
            try:
                item = UnverifiedRecord.model_validate(record)
            except Exception:  # pylint: disable=broad-except
                return {"passed": False, "reason": "unverified_records_invalid"}
            if item.unverified_id in target_map:
                return {"passed": False, "reason": "duplicate_unverified_id"}
            target_map[item.unverified_id] = item.model_dump(mode="json")
    old_target_records = {
        key: value
        for key, value in old_unverified.items()
        if (value["claim_id"], value["occurrence_id"]) == target
    }
    if not old_target_records:
        return {"passed": False, "reason": "target_unverified_record_missing"}
    new_target_pair = new_only_pairs[0]
    new_target_records = {
        key: value
        for key, value in new_unverified.items()
        if (value["claim_id"], value["occurrence_id"]) == new_target_pair
    }
    if set(new_target_records) != set(old_target_records):
        return {"passed": False, "reason": "target_unverified_record_changed"}
    for record_id, old_record in old_target_records.items():
        new_record = new_target_records[record_id]
        if any(
            new_record.get(key) != old_record.get(key)
            for key in (
                "unverified_id",
                "status",
                "reason",
                "expected_evidence_types",
            )
        ):
            return {"passed": False, "reason": "target_unverified_record_changed"}
    old_unrelated_records = {
        key: value for key, value in old_unverified.items()
        if key not in old_target_records
    }
    new_unrelated_records = {
        key: value for key, value in new_unverified.items()
        if key not in new_target_records
    }
    if old_unrelated_records != new_unrelated_records:
        return {"passed": False, "reason": "unrelated_unverified_record_changed"}

    return {
        "passed": True,
        "reason": "localized_split_and_locked_metadata_preserved",
        "target_claim_id": target[0],
        "new_claim_id": new_only_pairs[0][0],
    }


def extract_locked_claim_evidence_content_conflicts(
    document: str,
    repair_inventory: dict[str, Any],
) -> dict[str, Any] | None:
    """Identify one cited occurrence incorrectly declared as unverified.

    This is deliberately limited to a protocol-only retry whose report body
    and citation declarations were already locked by the service. It does not
    infer missing source data or make a malformed protocol retryable.
    """

    if not claim_evidence_protocol_repair_body_matches(
        document, repair_inventory
    ):
        return None
    parsed = _parse_content_repair_payload(
        document, allow_locked_citations=True
    )
    if parsed is None:
        return None
    body, payload = parsed
    if body != repair_inventory.get("body"):
        return None
    if set(payload) != {"claims", "unverified_items"}:
        return None
    raw_claims = payload["claims"]
    anchors = _anchored_blocks(body)
    anchor_pairs = [
        (item.anchor, str(item.occurrence_id or "")) for item in anchors
    ]
    if (
        not anchors
        or any(not occurrence_id for _, occurrence_id in anchor_pairs)
        or len(anchor_pairs) != len(set(anchor_pairs))
    ):
        return None

    claim_kinds: dict[str, str] = {}
    for raw in raw_claims:
        try:
            claim = _ClaimKindDeclaration.model_validate(raw)
        except Exception:  # pylint: disable=broad-except
            return None
        if claim.claim_id in claim_kinds:
            return None
        claim_kinds[claim.claim_id] = claim.kind
    anchor_ids = {claim_id for claim_id, _ in anchor_pairs}
    if set(claim_kinds) != anchor_ids:
        return None

    unverified_records: dict[tuple[str, str], dict[str, Any]] = {}
    unverified_ids: set[str] = set()
    for raw in payload["unverified_items"]:
        try:
            item = UnverifiedRecord.model_validate(raw)
        except Exception:  # pylint: disable=broad-except
            return None
        key = (item.claim_id, str(item.occurrence_id or ""))
        if (
            not item.occurrence_id
            or item.unverified_id in unverified_ids
            or key not in anchor_pairs
            or claim_kinds.get(item.claim_id) != "unverified"
            or key in unverified_records
        ):
            return None
        unverified_ids.add(item.unverified_id)
        unverified_records[key] = item.model_dump(mode="json")
    expected_unverified = {
        (item.anchor, str(item.occurrence_id))
        for item in anchors
        if claim_kinds[item.anchor] == "unverified"
    }
    if set(unverified_records) != expected_unverified:
        return None

    conflicts = [
        {
            "claim_id": item.anchor,
            "occurrence_id": str(item.occurrence_id),
            "citation_ids": extract_task_citation_ids(item.text),
        }
        for item in anchors
        if claim_kinds[item.anchor] == "unverified"
        and extract_task_citation_ids(item.text)
    ]
    # The bounded content repair is only safe for one isolated occurrence.
    if len(conflicts) != 1:
        return None
    conflict = conflicts[0]
    marker = next(
        (
            match
            for match in _CLAIM_ANCHOR.finditer(body)
            if (match.group(1), match.group(2))
            == (conflict["claim_id"], conflict["occurrence_id"])
        ),
        None,
    )
    if marker is None:
        return None
    line_start = body.rfind("\n", 0, marker.start()) + 1
    line_end = body.find("\n", marker.end())
    if line_end < 0:
        line_end = len(body)
    line = body[line_start:line_end]
    if len(list(_CLAIM_ANCHOR.finditer(line))) != 1:
        return None

    return {
        "protocol_repair": repair_inventory,
        "conflict": conflict,
        "baseline_claims": raw_claims,
        "baseline_unverified_items": payload["unverified_items"],
    }


def materialize_locked_claim_evidence_content_repair(
    document: str,
    repair_inventory: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Materialize a localized content correction with server-locked sources.

    The model may split one conflicting occurrence into a cited claim and a
    separate unverified claim. All other body lines, claim declarations,
    unverified records, citation IDs, and citation source declarations remain
    fixed. The resulting document still goes through the normal validators.
    """

    parsed = _parse_content_repair_payload(
        document, allow_locked_citations=False
    )
    if parsed is None:
        return document, {
            "passed": False,
            "reason": "response_format_invalid",
        }
    body, payload = parsed
    scope = _validate_locked_content_repair_body(
        body, payload, repair_inventory
    )
    if not scope["passed"]:
        return document, scope

    original = repair_inventory.get("protocol_repair")
    if not isinstance(original, dict):
        return document, {
            "passed": False,
            "reason": "locked_source_inventory_missing",
        }
    body_anchors = [
        [match.group(1), match.group(2)]
        for match in _CLAIM_ANCHOR.finditer(body)
    ]
    updated_inventory = dict(original)
    updated_inventory.update(
        {
            "body": body,
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "anchor_occurrences": body_anchors,
            "citation_ids": extract_task_citation_ids(body),
            "separator": _protocol_separator(body),
        }
    )
    finalized, actions = materialize_task_claim_evidence_protocol_repair(
        body,
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ),
        updated_inventory,
        derive_locked_bindings=True,
    )
    if not actions:
        return document, {
            "passed": False,
            "reason": "protocol_materialization_refused",
        }
    protocol_lock_check = validate_locked_claim_evidence_content_repair(
        finalized, repair_inventory
    )
    if not protocol_lock_check.get("passed"):
        return document, {
            "passed": False,
            "reason": "materialized_protocol_lock_check_failed",
            "protocol_lock_check": protocol_lock_check,
        }
    return finalized, {
        "passed": True,
        "status": "materialized",
        "actions": actions,
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "scope": scope,
        "protocol_lock_check": protocol_lock_check,
    }


def validate_locked_claim_evidence_content_repair(
    document: str,
    repair_inventory: dict[str, Any],
) -> dict[str, Any]:
    """Recheck a materialized localized repair before normal ledger checks."""

    original = repair_inventory.get("protocol_repair")
    if not isinstance(original, dict) or not _locked_citations_match(
        document, original
    ):
        return {"passed": False, "reason": "locked_citations_changed"}
    parsed = _parse_content_repair_payload(
        document, allow_locked_citations=True
    )
    if parsed is None:
        return {"passed": False, "reason": "materialized_document_invalid"}
    body, payload = parsed
    if set(payload) != {"claims", "bindings", "unverified_items"}:
        return {"passed": False, "reason": "materialized_protocol_invalid"}
    compact_payload: dict[str, Any] = {
        "claims": [
            {"claim_id": item.get("claim_id"), "kind": item.get("kind")}
            for item in payload["claims"]
            if isinstance(item, dict)
        ],
        "unverified_items": payload["unverified_items"],
    }
    if len(compact_payload["claims"]) != len(payload["claims"]):
        return {"passed": False, "reason": "materialized_claims_invalid"}
    return _validate_locked_content_repair_body(
        body, compact_payload, repair_inventory
    )


def claim_evidence_protocol_envelope_failure_is_retryable(
    diagnostics: dict[str, list[dict[str, Any]]] | None,
) -> bool:
    """Permit one repair attempt only for missing/incomplete protocol blocks."""

    if not isinstance(diagnostics, dict) or diagnostics.get("source"):
        return False
    claim_envelope_codes = {
        "claim_block_count",  # legacy diagnostic
        "claim_block_missing",
        "incomplete_claim_block",
    }
    citation_envelope_codes = {
        "citation_protocol_failed",  # legacy diagnostic
        "citation_block_missing",
        "incomplete_citation_block",
        "content_after_citation_block",
    }
    found: set[str] = set()
    for category, items in diagnostics.items():
        if not items:
            continue
        if category not in {"structure", "citation", "unverified"} or not isinstance(items, list):
            return False
        for item in items:
            if not isinstance(item, dict):
                return False
            code = str(item.get("error_code"))
            if category == "structure" and code in claim_envelope_codes:
                found.add(code)
            elif category == "citation" and code in citation_envelope_codes:
                found.add(code)
            else:
                return False
    claim_codes = found.intersection(claim_envelope_codes)
    citation_codes = found.intersection(citation_envelope_codes)
    return len(claim_codes) == 1 and len(citation_codes) <= 1


def compare_claim_evidence_inventories(
    previous: dict[str, list[Any]] | None,
    current: dict[str, list[Any]] | None,
) -> dict[str, Any]:
    """Ensure a retry preserves all previously declared stable identities."""

    if previous is None or current is None:
        return {
            "passed": False,
            "missing_claim_ids": [],
            "changed_claim_ids": [],
            "missing_occurrences": [],
            "changed_occurrences": [],
            "missing_unverified_ids": [],
            "changed_unverified_ids": [],
            "reason": "无法安全解析前后 Claim/Evidence 协议清单。",
        }
    current_claim_ids = set(current.get("claim_ids", []))
    current_claims = current.get("claims", {})
    previous_claims = previous.get("claims", {})
    changed_claim_ids = sorted(
        claim_id
        for claim_id in set(previous_claims) & set(current_claims)
        if previous_claims[claim_id] != current_claims[claim_id]
    )
    current_occurrences = {
        (str(item.get("claim_id")), str(item.get("occurrence_id"))): {
            "text": item.get("text"),
            "citation_ids": item.get("citation_ids"),
        }
        for item in current.get("occurrences", [])
        if isinstance(item, dict)
    }
    previous_occurrences = {
        (str(item.get("claim_id")), str(item.get("occurrence_id"))): {
            "text": item.get("text"),
            "citation_ids": item.get("citation_ids"),
        }
        for item in previous.get("occurrences", [])
        if isinstance(item, dict)
    }
    current_unverified_ids = set(current.get("unverified_ids", []))
    previous_unverified_items = previous.get("unverified_items", {})
    current_unverified_items = current.get("unverified_items", {})
    changed_unverified_ids = sorted(
        item_id
        for item_id in (
            set(previous_unverified_items) & set(current_unverified_items)
        )
        if previous_unverified_items[item_id]
        != current_unverified_items[item_id]
    )
    missing_claim_ids = sorted(
        set(previous.get("claim_ids", [])) - current_claim_ids
    )
    missing_occurrences = [
        {"claim_id": claim_id, "occurrence_id": occurrence_id}
        for claim_id, occurrence_id in previous_occurrences
        if (claim_id, occurrence_id) not in current_occurrences
    ]
    changed_occurrences = [
        {"claim_id": claim_id, "occurrence_id": occurrence_id}
        for (claim_id, occurrence_id), content in previous_occurrences.items()
        if (claim_id, occurrence_id) in current_occurrences
        and current_occurrences[(claim_id, occurrence_id)] != content
    ]
    missing_unverified_ids = sorted(
        set(previous.get("unverified_ids", [])) - current_unverified_ids
    )
    missing_claim_ids = sorted(
        set(previous.get("claim_ids", [])) - current_claim_ids
    )
    passed = not (
        missing_claim_ids
        or changed_claim_ids
        or missing_occurrences
        or changed_occurrences
        or missing_unverified_ids
        or changed_unverified_ids
    )
    return {
        "passed": passed,
        "missing_claim_ids": missing_claim_ids,
        "changed_claim_ids": changed_claim_ids,
        "missing_occurrences": missing_occurrences,
        "changed_occurrences": changed_occurrences,
        "missing_unverified_ids": missing_unverified_ids,
        "changed_unverified_ids": changed_unverified_ids,
        "reason": ""
        if passed
        else "受限修复尝试删除或改写原有声明/待核实项。",
    }


def claim_evidence_failure_is_retryable(
    diagnostics: dict[str, list[dict[str, Any]]] | None,
) -> bool:
    """Allow only known location/linkage repairs, never source failures."""

    if not isinstance(diagnostics, dict):
        return False
    if diagnostics.get("source"):
        return False
    allowed_codes = {
        "structure": {
            "claim_anchor_count",
            "claim_text_not_at_anchor",
            "unregistered_claim_anchor",
            "unregistered_claim_occurrence",
            "occurrence_anchor_count",
            "occurrence_text_not_at_anchor",
            "multiple_claims_in_table_row",
        },
        "citation": {
            "binding_not_declared_by_claim",
            "binding_claim_not_validated",
            "binding_occurrence_not_declared",
            "binding_not_declared_by_occurrence",
            "citation_without_claim_anchor",
            "claim_marker_mismatch",
            "occurrence_marker_mismatch",
        },
        "unverified": {
            "unverified_claim_missing",
            "unverified_claim_not_validated",
            "unverified_occurrence_missing",
        },
    }
    found = False
    for category, items in diagnostics.items():
        if not items:
            continue
        if category not in allowed_codes or not isinstance(items, list):
            return False
        for item in items:
            if not isinstance(item, dict) or item.get("error_code") not in allowed_codes[category]:
                return False
            found = True
    return found


def detect_claim_evidence_protocol(document: str) -> str | None:
    """Detect the protocol from its opening fence, even if output was cut off.

    Dispatch must not depend on a complete JSON block: an incomplete new
    contract should fail under the new validator, never fall through to the
    legacy S02 reader. A document containing both protocol markers is
    ambiguous and must fail closed.
    """

    has_current = bool(_CLAIM_BLOCK_START.search(document))
    has_legacy = bool(_LEGACY_CLAIM_BLOCK_START.search(document))
    if has_current and has_legacy:
        return "mixed"
    if has_current:
        return "claim_evidence"
    if has_legacy:
        return "legacy"
    return None


def validate_claim_evidence_document(
    document: str,
    evidence: list[dict[str, Any]],
    *,
    authorization_subject: str | None = None,
    citation_verification: dict[str, Any] | None = None,
    policy: EvidencePolicy | None = None,
) -> dict[str, Any]:
    """Validate anchored claims and per-claim bindings against a run snapshot.

    This verifies structure, ownership/source identity, exact quotes, stable
    body locations, and a conservative lexical association screen. It does not
    prove that evidence semantically entails a business conclusion.
    """

    groups = _empty_diagnostics()
    if len(document.encode("utf-8")) > _MAX_DOCUMENT_BYTES:
        _add(
            groups,
            "structure",
            "document_too_large",
            None,
            None,
            [],
            "核验文档超过 100 KB 限制。",
        )
        return _result(groups, [], [], [], None)

    normalized_evidence = normalize_evidence_snapshot(
        evidence,
        authorization_subject=authorization_subject,
    )
    citation_check = citation_verification
    if citation_check is None:
        citation_check = _empty_citation_verification(document)
    if citation_check is None:
        citation_check = verify_task_citations(
            document,
            normalized_evidence,
            authorization_subject=authorization_subject,
        )
    if not citation_check.get("passed"):
        citation_diagnostics = citation_check.get("diagnostics", [])
        if citation_diagnostics:
            for item in citation_diagnostics:
                if not isinstance(item, dict):
                    continue
                _add(
                    groups,
                    "citation",
                    str(item.get("error_code") or "citation_verification_failed"),
                    None,
                    item.get("location")
                    if isinstance(item.get("location"), str)
                    else None,
                    [],
                    str(item.get("message") or "引用核验失败。"),
                    phase=(
                        str(item.get("phase"))
                        if item.get("phase") is not None
                        else "citation_verification"
                    ),
                )
        else:
            for issue in citation_check.get("issues", []):
                _add(
                    groups,
                    "citation",
                    "citation_protocol_failed",
                    None,
                    None,
                    [],
                    str(issue),
                    phase="citation_verification",
                )

    blocks = list(_CLAIM_BLOCK.finditer(document))
    block_starts = list(_CLAIM_BLOCK_START.finditer(document))
    citation_starts = list(_CITATION_BLOCK_START.finditer(document))
    nested_protocol_start = bool(
        len(block_starts) == 1
        and len(blocks) == 1
        and any(
            block_starts[0].start() < start.start() < blocks[0].end()
            for start in citation_starts
        )
    )
    if len(blocks) != 1 or len(block_starts) != 1 or nested_protocol_start:
        if not block_starts:
            error_code = "claim_block_missing"
            location = None
            message = "缺少 task-claim-evidence JSON 区块。"
        elif len(block_starts) > 1:
            error_code = "duplicate_claim_block"
            location = f"char:{block_starts[1].start()}"
            message = "task-claim-evidence JSON 区块重复。"
        else:
            error_code = "incomplete_claim_block"
            location = f"char:{block_starts[0].start()}"
            message = "task-claim-evidence 代码围栏不完整。"
        _add(
            groups,
            "structure",
            error_code,
            None,
            location,
            [],
            message,
            phase="protocol_envelope",
        )
        return _result(
            groups,
            [],
            [],
            [],
            citation_check,
            protocol_parse_status="failed",
        )
    block = blocks[0]

    try:
        payload = json.loads(
            block.group(1), object_pairs_hook=unique_json_object
        )
    except DuplicateJSONKeyError as error:
        key = error.key if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", error.key) else "<invalid-key>"
        _add(
            groups,
            "structure",
            "duplicate_json_key",
            None,
            f"json-key:{key}",
            [],
            f"task-claim-evidence JSON 重复键：{key}。",
            phase="protocol_json",
        )
        return _result(
            groups,
            [],
            [],
            [],
            citation_check,
            protocol_parse_status="failed",
        )
    except json.JSONDecodeError as error:
        _add(
            groups,
            "structure",
            "invalid_json",
            None,
            f"line:{error.lineno}:column:{error.colno}:char:{error.pos}",
            [],
            f"task-claim-evidence JSON 语法错误，位于第 {error.lineno} 行第 {error.colno} 列。",
            phase="protocol_json",
        )
        return _result(
            groups,
            [],
            [],
            [],
            citation_check,
            protocol_parse_status="failed",
        )
    except (RecursionError, ValueError):
        _add(
            groups,
            "structure",
            "json_resource_or_value_error",
            None,
            "json-root",
            [],
            "task-claim-evidence JSON 超出解析深度或包含无效数值。",
            phase="protocol_json",
        )
        return _result(
            groups,
            [],
            [],
            [],
            citation_check,
            protocol_parse_status="failed",
        )
    if not isinstance(payload, dict) or set(payload) != {
        "claims",
        "bindings",
        "unverified_items",
    }:
        _add(
            groups,
            "structure",
            "invalid_top_level_fields",
            None,
            None,
            [],
            "协议必须且仅包含 claims、bindings 和 unverified_items。",
            phase="protocol_schema",
        )
        return _result(
            groups,
            [],
            [],
            [],
            citation_check,
            protocol_parse_status="failed",
        )

    raw_claims = payload["claims"]
    raw_bindings = payload["bindings"]
    raw_unverified = payload["unverified_items"]
    if not all(
        isinstance(value, list)
        for value in (raw_claims, raw_bindings, raw_unverified)
    ):
        _add(
            groups,
            "structure",
            "items_must_be_arrays",
            None,
            None,
            [],
            "claims、bindings 和 unverified_items 必须是数组。",
            phase="protocol_schema",
        )
        return _result(
            groups,
            [],
            [],
            [],
            citation_check,
            protocol_parse_status="failed",
        )
    if any(
        len(value) > _MAX_ITEMS
        for value in (raw_claims, raw_bindings, raw_unverified)
    ):
        _add(
            groups,
            "structure",
            "item_limit_exceeded",
            None,
            None,
            [],
            f"协议条目数不能超过 {_MAX_ITEMS}。",
            phase="protocol_schema",
        )
        return _result(
            groups,
            [],
            [],
            [],
            citation_check,
            protocol_parse_status="failed",
        )

    body = document[: block.start()]
    evidence_records, evidence_errors = _evidence_records(
        normalized_evidence,
        authorization_subject=authorization_subject,
    )
    for error in evidence_errors:
        _add(
            groups,
            "source",
            "invalid_evidence_snapshot",
            None,
            error.get("location"),
            [error["evidence_id"]] if error.get("evidence_id") else [],
            str(error["message"]),
        )
    verified_by_id = {
        item.get("citation_id"): item
        for item in citation_check.get("verified_citations", [])
        if isinstance(item, dict)
    }
    anchors = _anchored_blocks(body)
    anchors_by_claim: dict[str, list[_AnchoredBlock]] = {}
    anchors_by_key: dict[tuple[str, str | None], list[_AnchoredBlock]] = {}
    for anchored in anchors:
        anchors_by_claim.setdefault(anchored.anchor, []).append(anchored)
        anchors_by_key.setdefault(
            (anchored.anchor, anchored.occurrence_id), []
        ).append(anchored)

    claims: list[ClaimRecord] = []
    declared_claims: dict[str, ClaimRecord] = {}
    claims_by_id: dict[str, ClaimRecord] = {}
    claim_positions: dict[tuple[str, str | None], _AnchoredBlock] = {}
    declared_occurrences: dict[
        tuple[str, str], ClaimOccurrenceRecord
    ] = {}
    for index, raw in enumerate(raw_claims):
        claim = _parse_model(
            ClaimRecord,
            raw,
            groups,
            category="structure",
            error_code="invalid_claim_record",
            label=f"claims[{index}]",
        )
        if claim is None:
            continue
        if claim.claim_id in declared_claims:
            _add(
                groups,
                "structure",
                "duplicate_claim_id",
                claim.claim_id,
                claim.anchor,
                [],
                "claim_id 重复。",
            )
            continue
        declared_claims[claim.claim_id] = claim
        valid_claim = True
        if claim.kind == "template_limitation" and (
            policy is None
            or not policy.allowed_evidence_types_by_claim_kind.get(
                "template_limitation"
            )
        ):
            valid_claim = False
            _add(
                groups,
                "source",
                "claim_kind_not_allowed_by_policy",
                claim.claim_id,
                claim.anchor,
                [],
                "template_limitation 仅能在显式声明授权来源类型的场景策略中使用。",
            )
        if claim.anchor != claim.claim_id:
            valid_claim = False
            _add(
                groups,
                "structure",
                "anchor_mismatch",
                claim.claim_id,
                claim.anchor,
                [],
                "anchor 必须与 claim_id 一致。",
            )
        if len(set(claim.citation_ids)) != len(claim.citation_ids) or any(
            not _CITATION_ID.fullmatch(item) for item in claim.citation_ids
        ):
            valid_claim = False
            _add(
                groups,
                "structure",
                "invalid_claim_citation_ids",
                claim.claim_id,
                claim.anchor,
                [],
                "claim 的 citation_ids 格式无效或重复。",
            )

        positions: list[tuple[str | None, ClaimOccurrenceRecord | None, _AnchoredBlock]] = []
        if claim.occurrences:
            occurrence_ids = [item.occurrence_id for item in claim.occurrences]
            if len(occurrence_ids) != len(set(occurrence_ids)):
                valid_claim = False
                _add(
                    groups,
                    "structure",
                    "duplicate_occurrence_id",
                    claim.claim_id,
                    claim.anchor,
                    [],
                    "同一 claim 的 occurrence_id 必须唯一。",
                )
            union_citations = {
                citation_id
                for occurrence in claim.occurrences
                for citation_id in occurrence.citation_ids
            }
            if union_citations != set(claim.citation_ids):
                valid_claim = False
                _add(
                    groups,
                    "structure",
                    "claim_occurrence_citation_mismatch",
                    claim.claim_id,
                    claim.anchor,
                    [],
                    "claim.citation_ids 必须等于全部 occurrence 的引用编号并集。",
                )
            for occurrence in claim.occurrences:
                declared_occurrences[(claim.claim_id, occurrence.occurrence_id)] = occurrence
                if (
                    len(set(occurrence.citation_ids))
                    != len(occurrence.citation_ids)
                    or any(
                        not _CITATION_ID.fullmatch(item)
                        for item in occurrence.citation_ids
                    )
                ):
                    valid_claim = False
                    _add(
                        groups,
                        "structure",
                        "invalid_occurrence_citation_ids",
                        claim.claim_id,
                        occurrence.occurrence_id,
                        [],
                        "occurrence 的 citation_ids 格式无效或重复。",
                    )
                located = anchors_by_key.get(
                    (claim.claim_id, occurrence.occurrence_id), []
                )
                if len(located) != 1:
                    valid_claim = False
                    _add(
                        groups,
                        "structure",
                        "occurrence_anchor_count",
                        claim.claim_id,
                        occurrence.occurrence_id,
                        [],
                        "正文中的 claim occurrence 锚点缺失或重复。",
                    )
                    continue
                position = located[0]
                if not _claim_text_matches(occurrence.text, position.text):
                    valid_claim = False
                    _add(
                        groups,
                        "structure",
                        "occurrence_text_not_at_anchor",
                        claim.claim_id,
                        occurrence.occurrence_id,
                        [],
                        "occurrence 文本未出现在对应正文段落或表格行。",
                    )
                marker_ids = set(extract_task_citation_ids(position.text))
                if marker_ids != set(occurrence.citation_ids):
                    valid_claim = False
                    _add(
                        groups,
                        "citation",
                        "occurrence_marker_mismatch",
                        claim.claim_id,
                        occurrence.occurrence_id,
                        [],
                        "该正文位置的引用编号与 occurrence.citation_ids 不一致。",
                    )
                positions.append((occurrence.occurrence_id, occurrence, position))
        else:
            located = anchors_by_claim.get(claim.claim_id, [])
            if len(located) != 1 or located[0].occurrence_id is not None:
                valid_claim = False
                _add(
                    groups,
                    "structure",
                    "claim_anchor_count",
                    claim.claim_id,
                    claim.anchor,
                    [],
                    "正文中 claim 锚点缺失、重复或与新旧协议不一致。",
                )
            else:
                position = located[0]
                if not _claim_text_matches(claim.text, position.text):
                    valid_claim = False
                    _add(
                        groups,
                        "structure",
                        "claim_text_not_at_anchor",
                        claim.claim_id,
                        claim.anchor,
                        [],
                        "claim 文本未出现在对应正文段落或表格行。",
                    )
                if set(extract_task_citation_ids(position.text)) != set(
                    claim.citation_ids
                ):
                    valid_claim = False
                    _add(
                        groups,
                        "citation",
                        "claim_marker_mismatch",
                        claim.claim_id,
                        claim.anchor,
                        [],
                        "该正文位置的引用编号与 claim.citation_ids 不一致。",
                    )
                positions.append((None, None, position))

        for occurrence_id, occurrence, position in positions:
            citation_ids = (
                occurrence.citation_ids
                if occurrence is not None
                else claim.citation_ids
            )
            if claim.kind == "unverified" and citation_ids:
                valid_claim = False
                _add(
                    groups,
                    "structure",
                    "unverified_has_citations",
                    claim.claim_id,
                    occurrence_id or claim.anchor,
                    [],
                    "待核实 claim 不得伪关联已核验引用。",
                )
            elif (
                claim.kind
                in (
                    set(policy.require_citation_claim_kinds)
                    if policy is not None
                    else {
                        "fact",
                        "rule",
                        "historical_case",
                        "template_limitation",
                    }
                )
                and not citation_ids
            ):
                valid_claim = False
                _add(
                    groups,
                    "citation",
                    "claim_missing_citations",
                    claim.claim_id,
                    occurrence_id or claim.anchor,
                    [],
                    "当前 EvidencePolicy 要求该 claim occurrence 提供来源引用，或将无来源事实明确标为待核实。",
                )

        if valid_claim and positions:
            claims.append(claim)
            claims_by_id[claim.claim_id] = claim
            for occurrence_id, _, position in positions:
                claim_positions[(claim.claim_id, occurrence_id)] = position

    for anchored in anchors:
        declared = declared_claims.get(anchored.anchor)
        if declared is None:
            _add(
                groups,
                "structure",
                "unregistered_claim_anchor",
                None,
                anchored.anchor,
                [],
                "正文 claim 锚点对应的 claim_id 不存在于协议声明中。",
            )
        elif declared.occurrences and (
            anchored.occurrence_id is None
            or (anchored.anchor, anchored.occurrence_id)
            not in declared_occurrences
        ):
            _add(
                groups,
                "structure",
                "unregistered_claim_occurrence",
                anchored.anchor,
                anchored.occurrence_id or anchored.anchor,
                [],
                "正文 occurrence 锚点未在对应 claims[].occurrences 中声明。",
            )
        elif not declared.occurrences and anchored.occurrence_id is not None:
            _add(
                groups,
                "structure",
                "unregistered_claim_occurrence",
                anchored.anchor,
                anchored.occurrence_id,
                [],
                "旧版 claim 声明不能匹配新版 occurrence 锚点。",
            )

    for line in body.splitlines():
        if not (line.lstrip().startswith("|") or line.rstrip().endswith("|")):
            continue
        row_anchors = list(_CLAIM_ANCHOR.finditer(line))
        if len(row_anchors) > 1:
            for marker in row_anchors:
                _add(
                    groups,
                    "structure",
                    "multiple_claims_in_table_row",
                    marker.group(1),
                    marker.group(2) or marker.group(1),
                    [],
                    "每个表格行只能对应一个独立 claim occurrence；请拆分事实行。",
                )

    bindings: list[CitationBinding] = []
    binding_keys: set[tuple[str, str, str | None]] = set()
    bound_citations: dict[tuple[str, str | None], set[str]] = {}
    for index, raw in enumerate(raw_bindings):
        binding = _parse_model(
            CitationBinding,
            raw,
            groups,
            category="structure",
            error_code="invalid_binding_record",
            label=f"bindings[{index}]",
        )
        if binding is None:
            continue
        key = (
            binding.claim_id,
            binding.citation_id,
            binding.occurrence_id,
        )
        if key in binding_keys:
            _add(
                groups,
                "structure",
                "duplicate_binding",
                binding.claim_id,
                binding.occurrence_id,
                [binding.evidence_id],
                "同一 claim occurrence 与引用编号不能重复绑定。",
            )
            continue
        binding_keys.add(key)
        claim = declared_claims.get(binding.claim_id)
        if claim is None:
            _add(
                groups,
                "citation",
                "binding_claim_not_declared",
                binding.claim_id,
                binding.occurrence_id,
                [binding.evidence_id],
                "binding 对应的 claim_id 未在 claims 中声明。",
            )
            continue
        if binding.citation_id not in claim.citation_ids:
            _add(
                groups,
                "citation",
                "binding_not_declared_by_claim",
                binding.claim_id,
                binding.occurrence_id,
                [binding.evidence_id],
                "binding 的 citation_id 不在声明的 claim.citation_ids 中。",
            )
            continue
        occurrence = None
        position_key = (binding.claim_id, binding.occurrence_id)
        if claim.occurrences:
            occurrence = declared_occurrences.get(
                (binding.claim_id, binding.occurrence_id or "")
            )
            if binding.occurrence_id is None or occurrence is None:
                _add(
                    groups,
                    "citation",
                    "binding_occurrence_not_declared",
                    binding.claim_id,
                    binding.occurrence_id,
                    [binding.evidence_id],
                    "新版 binding 必须指向声明过的 occurrence_id。",
                )
                continue
            if binding.citation_id not in occurrence.citation_ids:
                _add(
                    groups,
                    "citation",
                    "binding_not_declared_by_occurrence",
                    binding.claim_id,
                    binding.occurrence_id,
                    [binding.evidence_id],
                    "binding 的 citation_id 未由该 occurrence 声明。",
                )
                continue
        elif binding.occurrence_id is not None:
            _add(
                groups,
                "citation",
                "binding_occurrence_not_declared",
                binding.claim_id,
                binding.occurrence_id,
                [binding.evidence_id],
                "旧版 claim 不包含 occurrence_id。",
            )
            continue

        if binding.claim_id not in claims_by_id or position_key not in claim_positions:
            _add(
                groups,
                "citation",
                "binding_claim_not_validated",
                binding.claim_id,
                binding.occurrence_id or claim.anchor,
                [binding.evidence_id],
                "binding 声明存在，但对应 claim occurrence 未通过正文锚点校验。",
            )
            continue
        position = claim_positions[position_key]
        claim_text = occurrence.text if occurrence is not None else claim.text
        source = evidence_records.get(binding.evidence_id)
        if source is None:
            _add(
                groups,
                "source",
                "evidence_id_not_in_run",
                binding.claim_id,
                binding.occurrence_id or claim.anchor,
                [binding.evidence_id],
                "evidence_id 不属于本次 TaskRun 的授权证据快照。",
            )
            continue
        if (
            authorization_subject
            and source.authorization_subject != authorization_subject
        ):
            _add(
                groups,
                "source",
                "evidence_owner_mismatch",
                binding.claim_id,
                binding.occurrence_id or claim.anchor,
                [binding.evidence_id],
                "证据授权主体与当前 TaskRun 所有者不一致。",
            )
            continue
        citation = verified_by_id.get(binding.citation_id)
        if (
            not isinstance(citation, dict)
            or citation.get("evidence_id") != binding.evidence_id
        ):
            _add(
                groups,
                "source",
                "citation_evidence_mismatch",
                binding.claim_id,
                binding.occurrence_id or claim.anchor,
                [binding.evidence_id],
                "引用编号对应的来源与 binding 声明的 evidence_id 不一致。",
            )
            continue
        allowed_evidence_types = (
            policy.allowed_evidence_types_by_claim_kind.get(claim.kind, [])
            if policy is not None
            else []
        )
        if (
            allowed_evidence_types
            and source.evidence_type not in allowed_evidence_types
        ):
            _add(
                groups,
                "source",
                "evidence_type_not_allowed",
                claim.claim_id,
                binding.occurrence_id or claim.anchor,
                [binding.evidence_id],
                (
                    f"{claim.kind} claim 不允许使用 "
                    f"{source.evidence_type} 类型来源。"
                ),
            )
            continue
        if binding.quote not in source.source_text:
            _add(
                groups,
                "source",
                "quote_not_in_source",
                binding.claim_id,
                binding.occurrence_id or claim.anchor,
                [binding.evidence_id],
                "claim 的原文摘录不属于所绑定来源片段。",
            )
            continue
        if not _lexically_associated(claim_text, binding.quote):
            _add(
                groups,
                "citation",
                "quote_unrelated_to_claim",
                binding.claim_id,
                binding.occurrence_id or claim.anchor,
                [binding.evidence_id],
                "原文摘录与 claim 缺少可确定匹配的数值或关键用语。",
            )
            continue
        bindings.append(binding)
        bound_citations.setdefault(position_key, set()).add(
            binding.citation_id
        )

    for claim in claims:
        if claim.kind == "unverified":
            continue
        occurrences = claim.occurrences or [None]
        for occurrence in occurrences:
            occurrence_id = (
                occurrence.occurrence_id if occurrence is not None else None
            )
            citation_ids = (
                occurrence.citation_ids
                if occurrence is not None
                else claim.citation_ids
            )
            missing = set(citation_ids) - bound_citations.get(
                (claim.claim_id, occurrence_id), set()
            )
            for citation_id in sorted(missing):
                citation = verified_by_id.get(citation_id)
                evidence_ids = (
                    [str(citation.get("evidence_id"))]
                    if isinstance(citation, dict) and citation.get("evidence_id")
                    else []
                )
                _add(
                    groups,
                    "citation",
                    "claim_binding_missing",
                    claim.claim_id,
                    occurrence_id or claim.anchor,
                    evidence_ids,
                    f"引用 {citation_id} 缺少该 claim occurrence 可通过来源与内容检查的有效原文摘录绑定。",
                )

    accepted_positions = list(claim_positions.values())
    for marker in re.finditer(r"\[C[1-9][0-9]{0,2}\]", body):
        if not any(
            item.block_start <= marker.start() < item.block_end
            for item in accepted_positions
        ):
            _add(
                groups,
                "citation",
                "citation_without_claim_anchor",
                None,
                f"offset:{marker.start()}",
                [],
                "正文引用编号没有映射到通过校验的 claim occurrence。",
            )

    unverified: list[UnverifiedRecord] = []
    seen_unverified_ids: set[str] = set()
    seen_unverified_positions: set[tuple[str, str | None]] = set()
    for index, raw in enumerate(raw_unverified):
        record = _parse_model(
            UnverifiedRecord,
            raw,
            groups,
            category="structure",
            error_code="invalid_unverified_record",
            label=f"unverified_items[{index}]",
        )
        if record is None:
            continue
        claim = declared_claims.get(record.claim_id)
        if record.unverified_id in seen_unverified_ids:
            _add(
                groups,
                "unverified",
                "duplicate_unverified_id",
                record.claim_id,
                record.occurrence_id or (claim.anchor if claim else None),
                [],
                "unverified_id 重复。",
                unverified_id=record.unverified_id,
            )
            continue
        if claim is None or claim.kind != "unverified":
            _add(
                groups,
                "unverified",
                "unverified_claim_missing",
                record.claim_id,
                record.occurrence_id,
                [],
                "待核实记录必须关联已声明的 unverified claim。",
                unverified_id=record.unverified_id,
            )
            continue
        position_key = (record.claim_id, record.occurrence_id)
        if claim.occurrences:
            if record.occurrence_id is None or (
                record.claim_id,
                record.occurrence_id,
            ) not in declared_occurrences:
                _add(
                    groups,
                    "unverified",
                    "unverified_occurrence_missing",
                    record.claim_id,
                    record.occurrence_id,
                    [],
                    "新版待核实记录必须关联已声明的 claim occurrence。",
                    unverified_id=record.unverified_id,
                )
                continue
        elif record.occurrence_id is not None:
            _add(
                groups,
                "unverified",
                "unverified_occurrence_missing",
                record.claim_id,
                record.occurrence_id,
                [],
                "旧版待核实 claim 不包含 occurrence_id。",
                unverified_id=record.unverified_id,
            )
            continue
        if record.claim_id not in claims_by_id or position_key not in claim_positions:
            _add(
                groups,
                "unverified",
                "unverified_claim_not_validated",
                record.claim_id,
                record.occurrence_id or claim.anchor,
                [],
                "待核实记录的正文位置未通过 claim occurrence 校验。",
                unverified_id=record.unverified_id,
            )
            continue
        if position_key in seen_unverified_positions:
            _add(
                groups,
                "unverified",
                "duplicate_unverified_occurrence",
                record.claim_id,
                record.occurrence_id or claim.anchor,
                [],
                "同一 claim occurrence 不能重复登记待核实事项。",
                unverified_id=record.unverified_id,
            )
            continue
        seen_unverified_ids.add(record.unverified_id)
        seen_unverified_positions.add(position_key)
        unverified.append(record)

    for claim in claims:
        if claim.kind != "unverified":
            continue
        occurrence_ids = [
            item.occurrence_id for item in claim.occurrences
        ] or [None]
        for occurrence_id in occurrence_ids:
            if (claim.claim_id, occurrence_id) in seen_unverified_positions:
                continue
            _add(
                groups,
                "unverified",
                "unverified_record_missing",
                claim.claim_id,
                occurrence_id or claim.anchor,
                [],
                "unverified claim occurrence 缺少独立 unverified_id、原因或预期证据类型。",
            )

    result = _result(groups, claims, bindings, unverified, citation_check)
    if policy is not None:
        result["verification_scope"]["policy_id"] = policy.policy_id
        result["verification_scope"]["semantic_review_claim_ids"] = [
            claim.claim_id
            for claim in claims
            if claim.kind in policy.manual_review_claim_kinds
        ]
    return result


def normalize_task_protocol_format(document: str) -> tuple[str, list[str]]:
    """Canonicalize whitespace inside unique, valid Task protocol JSON blocks.

    Parsing rejects duplicate JSON keys and only reserializes JSON objects. It
    never repairs missing fields, identifiers, evidence, quotes, or wrappers.
    Invalid or ambiguous blocks are returned unchanged for normal validation.
    """

    if len(document.encode("utf-8")) > _MAX_DOCUMENT_BYTES:
        return document, []
    selected: list[tuple[str, re.Pattern[str], dict[str, Any]]] = []
    for label, pattern in (
        ("task-claim-evidence", _CLAIM_BLOCK),
        ("task-citations", _CITATIONS_BLOCK),
    ):
        blocks = list(pattern.finditer(document))
        if len(blocks) > 1:
            return document, []
        if len(blocks) != 1:
            continue
        block = blocks[0]
        try:
            payload = json.loads(
                block.group(1), object_pairs_hook=unique_json_object
            )
        except (json.JSONDecodeError, RecursionError, ValueError):
            return document, []
        if not isinstance(payload, dict):
            return document, []
        selected.append((label, pattern, payload))

    normalized = document
    actions: list[str] = []
    for label, pattern, payload in selected:
        block = list(pattern.finditer(normalized))[0]
        try:
            canonical = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, RecursionError):
            return document, []
        if canonical != block.group(1):
            normalized = (
                normalized[: block.start(1)]
                + canonical
                + normalized[block.end(1) :]
            )
            actions.append(f"canonicalized_{label}_json_whitespace")
    return normalized, actions


def _evidence_records(
    evidence: list[dict[str, Any]],
    *,
    authorization_subject: str | None,
) -> tuple[dict[str, EvidenceRecord], list[dict[str, str | None]]]:
    records: dict[str, EvidenceRecord] = {}
    issues: list[dict[str, str | None]] = []
    normalized = normalize_evidence_snapshot(
        evidence,
        authorization_subject=authorization_subject,
    )
    for index, item in enumerate(normalized):
        source_ref = (
            item.get("document_id")
            or item.get("record_id")
            or item.get("call_id")
            or item.get("tool_name")
            or item.get("locator")
        )
        evidence_id = item.get("evidence_id")
        try:
            raw_location = {
                "knowledge_base_id": item.get("knowledge_base_id"),
                "document_id": item.get("document_id"),
                "chunk_index": item.get("chunk_index"),
                "record_id": item.get("record_id"),
                "call_id": item.get("call_id"),
                "field_path": item.get("field_path"),
                "locator": item.get("locator"),
            }
            record = EvidenceRecord.model_validate(
                {
                    "evidence_id": item.get("evidence_id"),
                    "source_type": item.get("source_type"),
                    "authorization_subject": item.get("authorization_subject"),
                    "source_id": (
                        item.get("source_id")
                        or item.get("document_id")
                        or item.get("record_id")
                        or item.get("call_id")
                        or item.get("tool_name")
                    ),
                    "knowledge_base_name": item.get("knowledge_base_name"),
                    "source": item.get("source"),
                    "location": raw_location,
                    "content_sha256": item.get("content_sha256"),
                    "source_text": item.get("source_text"),
                    "source_text_truncated": item.get(
                        "source_text_truncated", False
                    ),
                    "collected_at": item.get("collected_at"),
                    "source_updated_at": item.get("source_updated_at"),
                    "evidence_type": item.get("evidence_type", "unspecified"),
                }
            )
        except Exception as error:  # Pydantic gives structured field details.
            issues.append(
                {
                    "evidence_id": evidence_id,
                    "location": str(source_ref) if source_ref else None,
                    "message": f"evidence[{index}] 字段无效：{error}",
                },
            )
            continue
        if record.authorization_subject != (
            authorization_subject or record.authorization_subject
        ):
            issues.append(
                {
                    "evidence_id": record.evidence_id,
                    "location": str(source_ref) if source_ref else None,
                    "message": f"evidence[{index}] 授权主体与 TaskRun 所有者不一致。",
                },
            )
            continue
        if item.get("source_status", "ready") != "ready":
            issues.append(
                {
                    "evidence_id": record.evidence_id,
                    "location": str(source_ref) if source_ref else None,
                    "message": f"evidence[{index}] 来源状态不是 ready。",
                },
            )
            continue
        if item.get("source_text_truncated") is False:
            import hashlib

            digest = hashlib.sha256(
                record.source_text.encode("utf-8")
            ).hexdigest()
            if digest != record.content_sha256:
                issues.append(
                    {
                        "evidence_id": record.evidence_id,
                        "location": str(source_ref) if source_ref else None,
                        "message": f"evidence[{index}] 原文片段哈希不匹配。",
                    },
                )
                continue
        if record.evidence_id in records:
            issues.append(
                {
                    "evidence_id": record.evidence_id,
                    "location": str(source_ref) if source_ref else None,
                    "message": f"evidence[{index}] evidence_id 重复。",
                },
            )
            continue
        records[record.evidence_id] = record
    return records, issues


def materialize_task_citations_from_bindings(
    document: str,
    evidence: list[dict[str, Any]],
    *,
    authorization_subject: str | None = None,
    policy: EvidencePolicy | None = None,
) -> tuple[str, list[str]]:
    """Append a citation index only from complete, unambiguous bindings.

    This is a representation helper, not a verifier. The caller must still run
    ``verify_task_citations`` and ``validate_claim_evidence_document`` on the
    returned document. Any malformed, partial, ambiguous, or unauthorized
    input is returned unchanged so normal validation can fail closed.
    """

    if len(document.encode("utf-8")) > _MAX_DOCUMENT_BYTES:
        return document, []
    if detect_claim_evidence_protocol(document) != "claim_evidence":
        return document, []
    if re.search(r"```task-citations(?=[ \t\r\n]|$)", document):
        # Existing, duplicate, or incomplete citation fences belong to normal
        # validation; never rewrite them into an apparently valid protocol.
        return document, []

    claim_blocks = list(_CLAIM_BLOCK.finditer(document))
    claim_starts = list(_CLAIM_BLOCK_START.finditer(document))
    if len(claim_blocks) != 1 or len(claim_starts) != 1:
        return document, []
    claim_block = claim_blocks[0]
    if document[claim_block.end() :].strip():
        return document, []

    try:
        payload = json.loads(
            claim_block.group(1), object_pairs_hook=unique_json_object
        )
    except (json.JSONDecodeError, RecursionError, ValueError):
        return document, []
    if not isinstance(payload, dict) or set(payload) != {
        "claims",
        "bindings",
        "unverified_items",
    }:
        return document, []
    raw_claims = payload["claims"]
    raw_bindings = payload["bindings"]
    raw_unverified = payload["unverified_items"]
    if not all(
        isinstance(items, list) and len(items) <= _MAX_ITEMS
        for items in (raw_claims, raw_bindings, raw_unverified)
    ):
        return document, []

    claims: dict[str, ClaimRecord] = {}
    required_citation_kinds = set(
        policy.require_citation_claim_kinds
        if policy is not None
        else (
            "fact",
            "rule",
            "historical_case",
            "template_limitation",
        )
    )
    for raw in raw_claims:
        try:
            claim = ClaimRecord.model_validate(raw)
        except Exception:  # pylint: disable=broad-except
            return document, []
        if (
            claim.claim_id in claims
            or claim.anchor != claim.claim_id
            or len(set(claim.citation_ids)) != len(claim.citation_ids)
            or any(not _CITATION_ID.fullmatch(item) for item in claim.citation_ids)
        ):
            return document, []
        occurrence_ids = [item.occurrence_id for item in claim.occurrences]
        if len(occurrence_ids) != len(set(occurrence_ids)):
            return document, []
        occurrence_citations = [
            citation_id
            for occurrence in claim.occurrences
            for citation_id in occurrence.citation_ids
        ]
        if (
            any(
                len(set(occurrence.citation_ids))
                != len(occurrence.citation_ids)
                or any(
                    not _CITATION_ID.fullmatch(item)
                    for item in occurrence.citation_ids
                )
                for occurrence in claim.occurrences
            )
            or (
                claim.occurrences
                and set(occurrence_citations) != set(claim.citation_ids)
            )
            or (
                claim.kind == "unverified" and claim.citation_ids
            )
            or (
                claim.kind in required_citation_kinds
                and not claim.citation_ids
            )
        ):
            return document, []
        claims[claim.claim_id] = claim

    unverified_ids: set[str] = set()
    for raw in raw_unverified:
        try:
            item = UnverifiedRecord.model_validate(raw)
        except Exception:  # pylint: disable=broad-except
            return document, []
        claim = claims.get(item.claim_id)
        if (
            item.unverified_id in unverified_ids
            or claim is None
            or claim.kind != "unverified"
            or (
                claim.occurrences
                and item.occurrence_id
                not in {entry.occurrence_id for entry in claim.occurrences}
            )
            or (not claim.occurrences and item.occurrence_id is not None)
        ):
            return document, []
        unverified_ids.add(item.unverified_id)

    normalized = normalize_evidence_snapshot(
        evidence,
        authorization_subject=authorization_subject,
    )
    evidence_records, evidence_errors = _evidence_records(
        normalized,
        authorization_subject=authorization_subject,
    )
    if evidence_errors:
        return document, []

    citations_by_id: dict[str, dict[str, str]] = {}
    binding_keys: set[tuple[str, str | None, str]] = set()
    for raw in raw_bindings:
        try:
            binding = CitationBinding.model_validate(raw)
        except Exception:  # pylint: disable=broad-except
            return document, []
        claim = claims.get(binding.claim_id)
        if claim is None or claim.kind == "unverified":
            return document, []
        if binding.citation_id not in claim.citation_ids:
            return document, []
        if claim.occurrences:
            occurrence = next(
                (
                    item
                    for item in claim.occurrences
                    if item.occurrence_id == binding.occurrence_id
                ),
                None,
            )
            if (
                occurrence is None
                or binding.citation_id not in occurrence.citation_ids
            ):
                return document, []
        elif binding.occurrence_id is not None:
            return document, []

        key = (binding.claim_id, binding.occurrence_id, binding.citation_id)
        if key in binding_keys:
            return document, []
        binding_keys.add(key)
        source = evidence_records.get(binding.evidence_id)
        if (
            source is None
            or (
                authorization_subject
                and source.authorization_subject != authorization_subject
            )
            or binding.quote not in source.source_text
        ):
            return document, []
        existing = citations_by_id.get(binding.citation_id)
        if existing is not None:
            if existing["evidence_id"] != binding.evidence_id:
                # A single public citation ID cannot name two sources.
                return document, []
        else:
            citations_by_id[binding.citation_id] = {
                "citation_id": binding.citation_id,
                "evidence_id": binding.evidence_id,
                "quote": binding.quote,
            }

    expected_binding_keys = {
        (claim.claim_id, occurrence_id, citation_id)
        for claim in claims.values()
        if claim.kind != "unverified"
        for occurrence_id, citation_ids in (
            [
                (occurrence.occurrence_id, occurrence.citation_ids)
                for occurrence in claim.occurrences
            ]
            if claim.occurrences
            else [(None, claim.citation_ids)]
        )
        for citation_id in citation_ids
    }
    if binding_keys != expected_binding_keys:
        return document, []

    body = document[: claim_block.start()]
    body_citation_ids = set(extract_task_citation_ids(body))
    if body_citation_ids != set(citations_by_id):
        return document, []
    citations = [
        citations_by_id[citation_id]
        for citation_id in sorted(
            citations_by_id,
            key=lambda item: int(item[1:]),
        )
    ]
    if len(citations) > 64:
        return document, []
    block = json.dumps(
        {"citations": citations},
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    finalized = (
        document.rstrip()
        + "\n\n```task-citations\n"
        + block
        + "\n```"
    )
    if len(finalized.encode("utf-8")) > _MAX_DOCUMENT_BYTES:
        return document, []
    return finalized, ["materialized_task-citations_from_run_bindings"]


def _anchored_blocks(body: str) -> list[_AnchoredBlock]:
    lines = body.splitlines(keepends=True)
    offsets: list[tuple[int, int, str]] = []
    offset = 0
    for line in lines:
        offsets.append((offset, offset + len(line), line.rstrip("\r\n")))
        offset += len(line)
    blocks: list[_AnchoredBlock] = []
    all_markers = list(_CLAIM_ANCHOR.finditer(body))
    for marker in all_markers:
        line_entry = next(
            (
                entry
                for entry in offsets
                if entry[0] <= marker.start() < entry[1]
            ),
            (0, len(body), body),
        )
        line_start, line_end, line_text = line_entry
        if line_text.lstrip().startswith("|") or line_text.rstrip().endswith(
            "|"
        ):
            block_start, block_end, block_text = (
                line_start,
                line_end,
                line_text,
            )
        else:
            first = line_start
            last = line_end
            line_index = offsets.index(line_entry)
            while line_index > 0:
                prior = offsets[line_index - 1]
                if not prior[2].strip() or prior[2].lstrip().startswith("#"):
                    break
                first = prior[0]
                line_index -= 1
            line_index = offsets.index(line_entry)
            while line_index + 1 < len(offsets):
                following = offsets[line_index + 1]
                if not following[2].strip() or following[
                    2
                ].lstrip().startswith("#"):
                    break
                last = following[1]
                line_index += 1
            block_start, block_end = first, last
            block_text = body[block_start:block_end]
        paragraph_markers = [
            item
            for item in all_markers
            if block_start <= item.start() < block_end
        ]
        if len(paragraph_markers) > 1:
            # Multiple claim markers can occur in consecutive Markdown list
            # items without a blank line. In that case the whole paragraph is
            # not one occurrence: use the marker's physical line so a citation
            # on the next item cannot be attributed to the preceding claim.
            line_markers = [
                item
                for item in paragraph_markers
                if line_start <= item.start() < line_end
            ]
            if len(line_markers) <= 1:
                block_start, block_end, block_text = (
                    line_start,
                    line_end,
                    line_text,
                )
            else:
                marker_index = line_markers.index(marker)
                if marker_index > 0:
                    block_start = line_markers[marker_index - 1].end()
                else:
                    block_start = line_start
                block_end = marker.start()
                if not body[block_start:block_end].strip():
                    block_start = marker.end()
                    block_end = (
                        line_markers[marker_index + 1].start()
                        if marker_index + 1 < len(line_markers)
                        else line_end
                    )
                block_text = body[block_start:block_end]
        blocks.append(
            _AnchoredBlock(
                anchor=marker.group(1),
                occurrence_id=marker.group(2),
                marker_start=marker.start(),
                block_start=block_start,
                block_end=block_end,
                text=block_text,
            )
        )
    return blocks


def _claim_text_matches(claim_text: str, block_text: str) -> bool:
    expected = _normalize_markdown(claim_text)
    actual = _normalize_markdown(block_text)
    return bool(expected) and expected in actual


def _normalize_markdown(value: str) -> str:
    value = _CLAIM_ANCHOR.sub("", value)
    value = re.sub(r"\[C[1-9][0-9]{0,2}\]", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"[|*_`~]", "", value)
    value = re.sub(r"\\([|*_`])", r"\1", value)
    return re.sub(r"\s+", "", value).strip()


def _lexically_associated(claim: str, quote: str) -> bool:
    # Claim/occurrence IDs are protocol metadata, not facts to match against
    # the source. Their numeric suffixes must not be treated as claim values.
    claim_text = _CLAIM_ANCHOR.sub("", claim)
    claim_text = re.sub(r"\[C[1-9][0-9]{0,2}\]", "", claim_text)
    claim_text = re.sub(r"<[^>]+>", "", claim_text)
    claim_numbers = set(_NUMBER.findall(_IDENTIFIER.sub("", claim_text)))
    quote_numbers = set(_NUMBER.findall(_IDENTIFIER.sub("", quote)))
    if claim_numbers and not claim_numbers.issubset(quote_numbers):
        return False
    claim_identifiers = {
        re.sub(r"\s+", "", item).upper()
        for item in _IDENTIFIER.findall(claim_text)
    }
    quote_identifiers = {
        re.sub(r"\s+", "", item).upper() for item in _IDENTIFIER.findall(quote)
    }
    if claim_identifiers & quote_identifiers:
        return True
    claim_grams = _meaningful_han_bigrams(claim_text)
    quote_grams = _meaningful_han_bigrams(quote)
    return bool(claim_grams & quote_grams)


def _meaningful_han_bigrams(text: str) -> set[str]:
    grams: set[str] = set()
    for run in _HAN_RUN.findall(text):
        for index in range(len(run) - 1):
            gram = run[index : index + 2]
            if gram not in _STOP_TERMS:
                grams.add(gram)
    return grams


def _empty_citation_verification(document: str) -> dict[str, Any] | None:
    """Allow an explicitly empty citation ledger only when the body cites nothing.

    The standalone citation verifier intentionally requires at least one quote.
    Claim/Evidence documents may, however, contain only unresolved items. This
    narrow case does not attest to any source: exactly one empty citations block
    and no body citation markers are required. Any declared claim citations,
    bindings, or body markers are still checked by the normal ledger validator.
    """

    blocks = list(_CITATIONS_BLOCK.finditer(document))
    if len(blocks) != 1 or document[blocks[0].end() :].strip():
        return None
    if extract_task_citation_ids(document[: blocks[0].start()]):
        return None
    try:
        payload = json.loads(
            blocks[0].group(1), object_pairs_hook=unique_json_object
        )
    except (json.JSONDecodeError, RecursionError, ValueError):
        return None
    if payload != {"citations": []}:
        return None
    return {
        "passed": True,
        "issues": [],
        "verified_citations": [],
        "verification_scope": {
            "citation_id_set": "passed",
            "source_identity_and_quote": "not_applicable",
            "semantic_support": "not_automatically_evaluated",
        },
    }


def _parse_model(
    model: type[BaseModel],
    raw: Any,
    groups: dict[str, list[dict[str, Any]]],
    *,
    category: str,
    error_code: str,
    label: str,
) -> Any | None:
    try:
        return model.model_validate(raw)
    except Exception as error:
        claim_id = raw.get("claim_id") if isinstance(raw, dict) else None
        unverified_id = (
            raw.get("unverified_id") if isinstance(raw, dict) else None
        )
        location = (
            raw.get("anchor") or raw.get("section")
            if isinstance(raw, dict)
            else None
        )
        schema_location = label
        schema_summary = "字段不符合协议"
        try:
            validation_errors = error.errors(
                include_input=False,
                include_context=False,
                include_url=False,
            )
        except (AttributeError, TypeError):
            validation_errors = []
        if validation_errors:
            first_error = validation_errors[0]
            location_parts = first_error.get("loc", [])
            if location_parts:
                schema_location += "." + ".".join(
                    str(part) for part in location_parts
                )
            schema_summary = str(first_error.get("type", "invalid"))[:80]
        _add(
            groups,
            category,
            error_code,
            claim_id if isinstance(claim_id, str) else None,
            schema_location if isinstance(schema_location, str) else location,
            [],
            f"{label} 字段不符合协议：{schema_summary}。",
            unverified_id=(
                unverified_id if isinstance(unverified_id, str) else None
            ),
            phase="protocol_schema",
        )
        return None


def _empty_diagnostics() -> dict[str, list[dict[str, Any]]]:
    return {
        "structure": [],
        "source": [],
        "citation": [],
        "unverified": [],
    }


def _add(
    groups: dict[str, list[dict[str, Any]]],
    category: str,
    error_code: str,
    claim_id: str | None,
    location: str | None,
    evidence_ids: list[str],
    message: str,
    *,
    unverified_id: str | None = None,
    phase: str | None = None,
) -> None:
    item = {
        "error_code": error_code,
        "claim_id": claim_id,
        "unverified_id": unverified_id,
        "location": location,
        "error_type": category,
        "evidence_ids": evidence_ids,
        "message": message,
    }
    if phase is not None:
        item["phase"] = phase
    groups[category].append(item)


def _result(
    groups: dict[str, list[dict[str, Any]]],
    claims: list[ClaimRecord],
    bindings: list[CitationBinding],
    unverified: list[UnverifiedRecord],
    citation_check: dict[str, Any] | None,
    *,
    protocol_parse_status: str = "passed",
) -> dict[str, Any]:
    diagnostics = [item for values in groups.values() for item in values]
    issues = list(dict.fromkeys(item["message"] for item in diagnostics))
    protocol_schema_failed = any(
        item.get("phase") == "protocol_schema"
        for item in groups["structure"]
    )
    protocol_failed = (
        protocol_parse_status != "passed" or protocol_schema_failed
    )
    binding_checks = "not_evaluated" if protocol_failed else None
    citation_scope = (
        citation_check.get("verification_scope", {})
        if isinstance(citation_check, dict)
        else {}
    )
    return {
        "passed": not diagnostics,
        "issues": issues,
        "diagnostics": groups,
        "all_diagnostics": diagnostics,
        "claims": [item.model_dump(mode="json") for item in claims],
        "bindings": [item.model_dump(mode="json") for item in bindings],
        "unverified_items": [
            item.model_dump(mode="json") for item in unverified
        ],
        "verification_scope": {
            "protocol_parse": (
                "failed" if protocol_parse_status != "passed" else "passed"
            ),
            "protocol_schema": (
                "failed" if protocol_schema_failed else "passed"
            ),
            "structure_and_locations": (
                "not_evaluated"
                if protocol_failed
                else "passed"
                if not groups["structure"]
                else "failed"
            ),
            "authorized_source_identity_and_hash": (
                binding_checks
                or ("passed" if not groups["source"] else "failed")
            ),
            "claim_quote_association": (
                binding_checks
                or ("passed" if not groups["citation"] else "failed")
            ),
            "unverified_mapping": (
                binding_checks
                or ("passed" if not groups["unverified"] else "failed")
            ),
            "semantic_support": (
                "not_evaluated" if protocol_failed else "requires_human_review"
            ),
            "citation_index_source_identity_and_quote": citation_scope.get(
                "source_identity_and_quote", "not_checked"
            ),
            "citation_ids": (
                citation_check.get("verification_scope", {}).get(
                    "citation_id_set", "not_checked"
                )
                if citation_check
                else "not_checked"
            ),
        },
    }


__all__ = [
    "ClaimRecord",
    "CitationBinding",
    "compare_claim_evidence_inventories",
    "diagnose_task_claim_evidence_protocol_materialization",
    "claim_evidence_failure_is_retryable",
    "claim_evidence_protocol_envelope_failure_is_retryable",
    "claim_evidence_protocol_repair_body_matches",
    "detect_claim_evidence_protocol",
    "EvidencePolicy",
    "EvidenceLocation",
    "EvidenceRecord",
    "TASK_CLAIM_EVIDENCE_PROMPT",
    "TASK_CLAIM_EVIDENCE_REPAIR_PROMPT",
    "UnverifiedRecord",
    "extract_claim_evidence_inventory",
    "extract_claim_evidence_protocol_repair_inventory",
    "extract_locked_claim_evidence_content_conflicts",
    "materialize_locked_claim_evidence_content_repair",
    "materialize_task_citations_from_bindings",
    "materialize_task_claim_evidence_protocol_repair",
    "wrap_claim_evidence_repair_payload_for_validation",
    "normalize_task_protocol_format",
    "normalize_evidence_snapshot",
    "validate_claim_evidence_document",
    "validate_locked_claim_evidence_content_repair",
]

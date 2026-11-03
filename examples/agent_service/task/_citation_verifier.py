# -*- coding: utf-8 -*-
"""Generic deterministic citation checks over one Task run's evidence."""

from __future__ import annotations

import json
import hashlib
import re
from typing import Any

_CITATION_BLOCK = re.compile(
    r"```task-citations[ \t]*\r?\n"
    r"(.*?)\r?\n```[ \t]*(?=\r?\n|$)",
    flags=re.DOTALL,
)
_CITATION_BLOCK_START = re.compile(r"```task-citations(?=[ \t\r\n]|$)")
_CITATION_ID = re.compile(r"^C[1-9][0-9]{0,2}$")
_CITATION_MARKER = re.compile(r"\[(C[0-9]+)\]")
_MAX_CITATIONS = 64
_MAX_QUOTE_CHARS = 2_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DuplicateJSONKeyError(ValueError):
    """Raised when a protocol object repeats a JSON property name."""

    def __init__(self, key: str) -> None:
        super().__init__(f"duplicate JSON key: {key}")
        self.key = key

TASK_CITATION_PROMPT = """\
引用核验要求（Task 通用 AI verifier 会对照本次运行授权检索内容评估）：
1. 输出 Markdown 正文。每条来源事实都要在句末标注 [C1] 这类引用编号；建议、推断和待补充事项要明确标注。
2. 正文末尾必须附且仅附一个 `task-citations` 代码区块。内容为 JSON 对象：
   {"citations":[
     {"citation_id":"C1","evidence_id":"本次检索结果中的 evidence_id",
      "quote":"原文中的连续逐字摘录"}
   ]}
3. evidence_id 必须逐字取自上一检索节点保存的证据；不得编造 ID、哈希、段号或引文。历史任务仍可使用 knowledge_base_id、document_id、chunk_index、content_sha256 字段格式。
4. 只引用实际用于支持正文内容的来源；检索返回的候选来源不要求全部出现在报告中。不要把多个来源合并到同一引用编号。
5. quote 必须是来源正文中与所引内容直接相关的连续原文；不要只引用文件名、标题、工作表名或通用标签。不要改写、拼接或补全原文。
6. 每个正文引用编号必须在 JSON 中恰有一条记录；JSON 中未在正文使用的引用编号不得保留。最终整理时保留已核验引用编号和区块，不得添加没有来源支持的新事实。
7. AI 验收应对照本次运行授权检索内容评估编号、来源和摘录；语义支持仍需人工复核，不得声称服务端已自动证明。
"""


def verify_task_citations(
    document: str,
    evidence: list[dict[str, Any]],
    *,
    authorization_subject: str | None = None,
) -> dict[str, Any]:
    """Match structured citations to source records captured by this TaskRun."""

    issues: list[str] = []
    if len(document.encode("utf-8")) > 100_000:
        return _verification_result(
            ["待核验文档超过 100 KB 限制。"],
            [],
            citation_ids_match=False,
            source_quotes_match=False,
            diagnostics=[_diagnostic("document_too_large", "citation_parse", None, "待核验文档超过 100 KB 限制。")],
        )
    blocks = list(_CITATION_BLOCK.finditer(document))
    block_starts = list(_CITATION_BLOCK_START.finditer(document))
    if len(blocks) != 1 or len(block_starts) != 1:
        if not block_starts:
            error_code = "citation_block_missing"
            location = None
            message = (
                "文档必须包含且仅包含一个 task-citations JSON 区块；"
                "当前缺少该区块。"
            )
        elif len(block_starts) > 1:
            error_code = "duplicate_citation_block"
            location = f"char:{block_starts[1].start()}"
            message = (
                "文档必须包含且仅包含一个 task-citations JSON 区块；"
                "检测到重复区块。"
            )
        else:
            error_code = "incomplete_citation_block"
            location = f"char:{block_starts[0].start()}"
            message = (
                "文档必须包含且仅包含一个 task-citations JSON 区块；"
                "代码围栏不完整。"
            )
        return _verification_result(
            [message],
            [],
            citation_ids_match=False,
            source_quotes_match=False,
            diagnostics=[_diagnostic(error_code, "citation_envelope", location, message)],
        )
    if document[blocks[0].end() :].strip():
        return _verification_result(
            ["task-citations 区块之后不能追加未核验内容。"],
            [],
            citation_ids_match=False,
            source_quotes_match=False,
            diagnostics=[_diagnostic("content_after_citation_block", "citation_envelope", f"char:{blocks[0].end()}", "task-citations 区块之后不能追加未核验内容。")],
        )
    try:
        payload = json.loads(
            blocks[0].group(1),
            object_pairs_hook=unique_json_object,
        )
    except DuplicateJSONKeyError as error:
        key = error.key if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", error.key) else "<invalid-key>"
        message = f"task-citations JSON 重复键：{key}。"
        return _verification_result(
            [message],
            [],
            citation_ids_match=False,
            source_quotes_match=False,
            diagnostics=[_diagnostic("duplicate_json_key", "citation_json", f"json-key:{key}", message)],
        )
    except json.JSONDecodeError as error:
        message = (
            "task-citations 区块不是合法 JSON；语法错误位于第 "
            f"{error.lineno} 行第 {error.colno} 列。"
        )
        return _verification_result(
            [message],
            [],
            citation_ids_match=False,
            source_quotes_match=False,
            diagnostics=[_diagnostic("invalid_json", "citation_json", f"line:{error.lineno}:column:{error.colno}:char:{error.pos}", message)],
        )
    except (RecursionError, ValueError):
        message = "task-citations JSON 超出解析深度或包含无效数值。"
        return _verification_result(
            [message],
            [],
            citation_ids_match=False,
            source_quotes_match=False,
            diagnostics=[_diagnostic("json_resource_or_value_error", "citation_json", "json-root", message)],
        )
    if not isinstance(payload, dict) or set(payload) != {"citations"}:
        return _verification_result(
            ["task-citations JSON 必须只包含 citations 数组。"],
            [],
            citation_ids_match=False,
            source_quotes_match=False,
            diagnostics=[_diagnostic("citation_schema_mismatch", "citation_schema", "json-root", "task-citations JSON 必须只包含 citations 数组。")],
        )
    citations = payload["citations"]
    if not isinstance(citations, list):
        return _verification_result(
            ["task-citations.citations 必须是数组。"],
            [],
            citation_ids_match=False,
            source_quotes_match=False,
            diagnostics=[_diagnostic("citations_not_array", "citation_schema", "citations", "task-citations.citations 必须是数组。")],
        )
    if not citations:
        body = document[: blocks[0].start()]
        if _CITATION_MARKER.search(body):
            return _verification_result(
                ["正文包含引用编号，但 task-citations.citations 为空。"],
                [],
                citation_ids_match=False,
                source_quotes_match=False,
                diagnostics=[_diagnostic("empty_citations_with_body_markers", "citation_schema", "citations", "正文包含引用编号，但 task-citations.citations 为空。")],
            )
        return _verification_result(
            [],
            [],
            citation_ids_match=True,
            source_quotes_match=True,
        )
    if len(citations) > _MAX_CITATIONS:
        return _verification_result(
            [f"引用条目不能超过 {_MAX_CITATIONS} 条。"],
            [],
            citation_ids_match=False,
            source_quotes_match=False,
            diagnostics=[_diagnostic("citation_limit_exceeded", "citation_schema", "citations", f"引用条目不能超过 {_MAX_CITATIONS} 条。")],
        )

    evidence_by_identity = {
        (
            str(item.get("knowledge_base_id", "")),
            str(item.get("document_id", "")),
            item.get("chunk_index"),
            str(item.get("content_sha256", "")),
        ): item
        for item in evidence
        if isinstance(item, dict)
    }
    evidence_by_id = {
        _task_evidence_id(
            item,
            str(item.get("authorization_subject") or ""),
        ): item
        for item in evidence
        if isinstance(item, dict)
    }
    body = document[: blocks[0].start()]
    body_markers = set(_CITATION_MARKER.findall(body))
    seen_ids: set[str] = set()
    verified: list[dict[str, Any]] = []
    source_quotes_match = True
    legacy_fields = {
        "citation_id",
        "knowledge_base_id",
        "document_id",
        "chunk_index",
        "content_sha256",
        "quote",
    }
    evidence_fields = {"citation_id", "evidence_id", "quote"}
    for index, citation in enumerate(citations):
        prefix = f"citations[{index}]"
        if not isinstance(citation, dict) or set(citation) not in (
            legacy_fields,
            evidence_fields,
        ):
            issues.append(f"{prefix} 字段不符合引用协议。")
            continue
        citation_id = citation.get("citation_id")
        if not isinstance(citation_id, str) or not _CITATION_ID.fullmatch(
            citation_id
        ):
            issues.append(f"{prefix}.citation_id 格式无效。")
            continue
        if citation_id in seen_ids:
            issues.append(f"引用编号 {citation_id} 重复。")
            continue
        seen_ids.add(citation_id)
        if f"[{citation_id}]" not in body:
            issues.append(f"正文没有引用编号 [{citation_id}]。")

        is_evidence_reference = set(citation) == evidence_fields
        evidence_id = citation.get("evidence_id")
        kb_id = citation.get("knowledge_base_id")
        document_id = citation.get("document_id")
        chunk_index = citation.get("chunk_index")
        content_sha256 = citation.get("content_sha256")
        quote = citation.get("quote")
        valid_source_reference = (
            isinstance(evidence_id, str)
            and re.fullmatch(r"EV-[0-9a-f]{24}", evidence_id)
            if is_evidence_reference
            else (
                isinstance(kb_id, str)
                and isinstance(document_id, str)
                and isinstance(chunk_index, int)
                and not isinstance(chunk_index, bool)
                and chunk_index >= 0
                and isinstance(content_sha256, str)
            )
        )
        if not valid_source_reference or (
            not isinstance(quote, str)
            or not quote.strip()
            or len(quote) > _MAX_QUOTE_CHARS
        ):
            issues.append(f"{prefix} 的来源定位或原文摘录无效。")
            continue
        source = (
            evidence_by_id.get(evidence_id)
            if is_evidence_reference
            else evidence_by_identity.get(
                (kb_id, document_id, chunk_index, content_sha256),
            )
        )
        if source is None:
            issues.append(f"引用 {citation_id} 不属于本次运行检索到的授权来源。")
            source_quotes_match = False
            continue
        if (
            authorization_subject
            and source.get("authorization_subject") != authorization_subject
        ):
            issues.append(f"引用 {citation_id} 的授权主体与当前 TaskRun 所有者不一致。")
            source_quotes_match = False
            continue
        if is_evidence_reference:
            kb_id = source.get("knowledge_base_id")
            document_id = source.get("document_id")
            chunk_index = source.get("chunk_index")
            content_sha256 = source.get("content_sha256")
        source_text = source.get("source_text")
        if (
            source.get("source_status", "ready") != "ready"
            or not isinstance(source_text, str)
            or quote not in source_text
        ):
            issues.append(f"引用 {citation_id} 的摘录与保存的原文片段不一致。")
            source_quotes_match = False
            continue
        if not isinstance(content_sha256, str) or not _SHA256.fullmatch(
            content_sha256
        ):
            issues.append(f"引用 {citation_id} 的来源哈希格式无效。")
            source_quotes_match = False
            continue
        if source.get("source_text_truncated") is False:
            source_digest = hashlib.sha256(
                source_text.encode("utf-8"),
            ).hexdigest()
            if source_digest != content_sha256:
                issues.append(f"引用 {citation_id} 的来源哈希与保存原文不一致。")
                source_quotes_match = False
                continue
        verified.append(
            {
                "citation_id": citation_id,
                "evidence_id": _task_evidence_id(
                    source,
                    str(source.get("authorization_subject") or ""),
                ),
                "source_type": source.get(
                    "source_type", "knowledge_base_chunk"
                ),
                "knowledge_base_id": kb_id,
                "knowledge_base_name": source.get("knowledge_base_name"),
                "document_id": document_id,
                "source": source.get("source"),
                "chunk_index": chunk_index,
                "content_sha256": content_sha256,
                "quote": quote,
                "evidence_type": source.get("evidence_type", "unspecified"),
                "status": "verified",
                "semantic_support_status": "requires_human_review",
            },
        )

    unverified_markers = body_markers - seen_ids
    if unverified_markers:
        labels = "、".join(sorted(unverified_markers))
        issues.append(f"正文包含未列入核验清单的引用编号：{labels}。")

    citation_ids_match = body_markers == seen_ids and len(seen_ids) == len(
        citations
    )
    diagnostics = []
    for issue in issues:
        is_source_issue = any(
            term in issue for term in ("来源", "授权主体", "原文摘录", "哈希")
        )
        citation_match = re.search(r"引用\s+(C[1-9][0-9]{0,2})", issue)
        diagnostics.append(
            _diagnostic(
                "source_identity_or_quote_failed"
                if is_source_issue
                else "citation_schema_or_set_failed",
                "citation_source" if is_source_issue else "citation_consistency",
                f"citation:{citation_match.group(1)}"
                if citation_match
                else None,
                issue,
            )
        )
    return _verification_result(
        issues,
        verified,
        citation_ids_match=citation_ids_match,
        source_quotes_match=(
            source_quotes_match and len(verified) == len(citations)
        ),
        diagnostics=diagnostics,
    )


def extract_task_citation_ids(text: str) -> list[str]:
    """Extract citation markers using the shared Task citation syntax."""

    return _CITATION_MARKER.findall(text)


def strip_task_citation_ids(text: str) -> str:
    """Remove citation markers using the shared Task citation syntax."""

    return _CITATION_MARKER.sub("", text)


def _verification_result(
    issues: list[str],
    verified: list[dict[str, Any]],
    *,
    citation_ids_match: bool,
    source_quotes_match: bool,
    diagnostics: list[dict[str, str | None]] | None = None,
) -> dict[str, Any]:
    return {
        "passed": not issues,
        "issues": issues,
        "diagnostics": diagnostics or [],
        "verified_citations": verified,
        "verification_scope": {
            "citation_id_set": "passed" if citation_ids_match else "failed",
            "source_identity_and_quote": (
                "passed" if source_quotes_match else "failed"
            ),
            "semantic_support": "not_automatically_evaluated",
        },
    }


def _diagnostic(
    error_code: str,
    phase: str,
    location: str | None,
    message: str,
) -> dict[str, str | None]:
    return {
        "error_code": error_code,
        "phase": phase,
        "location": location,
        "message": message,
    }


def _task_evidence_id(
    evidence: dict[str, Any],
    authorization_subject: str = "",
) -> str:
    """Derive a stable Task-local ID from server-owned source identity."""

    identity = {
        "source_type": evidence.get("source_type", "knowledge_base_chunk"),
        "authorization_subject": authorization_subject,
        "knowledge_base_id": evidence.get("knowledge_base_id"),
        "document_id": evidence.get("document_id"),
        "chunk_index": evidence.get("chunk_index"),
        "source_id": evidence.get("source_id"),
        "record_id": evidence.get("record_id"),
        "tool_name": evidence.get("tool_name"),
        "call_id": evidence.get("call_id"),
        "field_path": evidence.get("field_path"),
        "content_sha256": evidence.get("content_sha256"),
    }
    digest = hashlib.sha256(
        json.dumps(
            identity,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8"),
    ).hexdigest()
    return f"EV-{digest[:24]}"


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject repeated citation JSON keys instead of silently overwriting."""

    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise DuplicateJSONKeyError(key)
        payload[key] = value
    return payload


__all__ = [
    "TASK_CITATION_PROMPT",
    "_task_evidence_id",
    "extract_task_citation_ids",
    "strip_task_citation_ids",
    "unique_json_object",
    "verify_task_citations",
]

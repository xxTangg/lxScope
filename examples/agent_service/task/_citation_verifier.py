# -*- coding: utf-8 -*-
"""Deterministic citation checks over one Task run's retrieved evidence."""

from __future__ import annotations

import json
import re
from typing import Any


_CITATION_BLOCK = re.compile(
    r"```task-citations\s*\n(.*?)\n```",
    flags=re.DOTALL,
)
_CITATION_ID = re.compile(r"^C[1-9][0-9]{0,2}$")
_CITATION_MARKER = re.compile(r"\[(C[0-9]+)\]")
_MAX_CITATIONS = 64
_MAX_QUOTE_CHARS = 2_000

TASK_CITATION_PROMPT = """\
引用核验协议（Task 服务端会逐项比对本次运行保存的授权原文）：
1. 输出 Markdown 正文。每条来源事实都要在句末标注 [C1] 这类引用编号；建议、推断和待补充事项要明确标注。
2. 正文末尾必须附且仅附一个 `task-citations` 代码区块。内容为 JSON 对象：
   {"citations":[
     {"citation_id":"C1","knowledge_base_id":"知识库ID",
      "document_id":"文档ID","chunk_index":0,
      "content_sha256":"原文哈希","quote":"原文中的连续逐字摘录"}
   ]}
3. 每个引用字段必须逐字取自上一检索节点的 evidence；不得编造 ID、哈希、段号或引文。引用不匹配时 Task 会失败且不会生成文件。
4. 最终整理时保留已核验引用编号和 `task-citations` 区块；不得添加没有来源支持的新事实。
"""


def verify_task_citations(
    document: str,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """Match structured citations to exact chunks captured by the RAG step."""

    issues: list[str] = []
    if len(document.encode("utf-8")) > 100_000:
        return {
            "passed": False,
            "issues": ["待核验文档超过 100 KB 限制。"],
            "verified_citations": [],
        }
    blocks = list(_CITATION_BLOCK.finditer(document))
    if len(blocks) != 1:
        return {
            "passed": False,
            "issues": ["文档必须包含且仅包含一个 task-citations JSON 区块。"],
            "verified_citations": [],
        }
    if document[blocks[0].end() :].strip():
        return {
            "passed": False,
            "issues": ["task-citations 区块之后不能追加未核验内容。"],
            "verified_citations": [],
        }
    try:
        payload = json.loads(
            blocks[0].group(1),
            object_pairs_hook=_unique_json_object,
        )
    except (json.JSONDecodeError, RecursionError, ValueError):
        return {
            "passed": False,
            "issues": ["task-citations 区块不是合法 JSON。"],
            "verified_citations": [],
        }
    if not isinstance(payload, dict) or set(payload) != {"citations"}:
        return {
            "passed": False,
            "issues": ["task-citations JSON 必须只包含 citations 数组。"],
            "verified_citations": [],
        }
    citations = payload["citations"]
    if not isinstance(citations, list) or not citations:
        return {
            "passed": False,
            "issues": ["至少需要一条带原文摘录的引用。"],
            "verified_citations": [],
        }
    if len(citations) > _MAX_CITATIONS:
        return {
            "passed": False,
            "issues": [f"引用条目不能超过 {_MAX_CITATIONS} 条。"],
            "verified_citations": [],
        }

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
    body = document[: blocks[0].start()]
    body_markers = set(_CITATION_MARKER.findall(body))
    seen_ids: set[str] = set()
    verified: list[dict[str, Any]] = []
    expected_fields = {
        "citation_id",
        "knowledge_base_id",
        "document_id",
        "chunk_index",
        "content_sha256",
        "quote",
    }
    for index, citation in enumerate(citations):
        prefix = f"citations[{index}]"
        if not isinstance(citation, dict) or set(citation) != expected_fields:
            issues.append(f"{prefix} 字段不符合引用协议。")
            continue
        citation_id = citation.get("citation_id")
        if (
            not isinstance(citation_id, str)
            or not _CITATION_ID.fullmatch(citation_id)
        ):
            issues.append(f"{prefix}.citation_id 格式无效。")
            continue
        if citation_id in seen_ids:
            issues.append(f"引用编号 {citation_id} 重复。")
            continue
        seen_ids.add(citation_id)
        if f"[{citation_id}]" not in body:
            issues.append(f"正文没有引用编号 [{citation_id}]。")

        kb_id = citation.get("knowledge_base_id")
        document_id = citation.get("document_id")
        chunk_index = citation.get("chunk_index")
        content_sha256 = citation.get("content_sha256")
        quote = citation.get("quote")
        if (
            not isinstance(kb_id, str)
            or not isinstance(document_id, str)
            or not isinstance(chunk_index, int)
            or isinstance(chunk_index, bool)
            or chunk_index < 0
            or not isinstance(content_sha256, str)
            or not isinstance(quote, str)
            or not quote.strip()
            or len(quote) > _MAX_QUOTE_CHARS
        ):
            issues.append(f"{prefix} 的来源定位或原文摘录无效。")
            continue
        source = evidence_by_identity.get(
            (kb_id, document_id, chunk_index, content_sha256),
        )
        if source is None:
            issues.append(f"引用 {citation_id} 不属于本次运行检索到的授权来源。")
            continue
        source_text = source.get("source_text")
        if not isinstance(source_text, str) or quote not in source_text:
            issues.append(f"引用 {citation_id} 的摘录与保存的原文片段不一致。")
            continue
        verified.append(
            {
                "citation_id": citation_id,
                "knowledge_base_id": kb_id,
                "document_id": document_id,
                "source": source.get("source"),
                "chunk_index": chunk_index,
                "content_sha256": content_sha256,
                "quote": quote,
                "status": "verified",
            },
        )

    unverified_markers = body_markers - seen_ids
    if unverified_markers:
        labels = "、".join(sorted(unverified_markers))
        issues.append(f"正文包含未列入核验清单的引用编号：{labels}。")

    return {
        "passed": not issues and len(verified) == len(citations),
        "issues": issues,
        "verified_citations": verified,
    }


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject repeated citation JSON keys instead of silently overwriting."""

    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("Duplicate JSON key")
        payload[key] = value
    return payload


__all__ = ["TASK_CITATION_PROMPT", "verify_task_citations"]

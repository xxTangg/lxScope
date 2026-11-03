# -*- coding: utf-8 -*-
"""Task-owned adapter for AgentScope knowledge bases.

This module is deliberately a boundary, not a second knowledge-base
implementation.  It consumes the public ``KnowledgeBaseService`` methods,
keeps Task-specific selection/retrieval semantics local to the Task module,
and uses AgentScope's graph models/store for entity-relation extraction.
"""

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import SecretStr

from agentscope.app.storage import RedisKnowledgeGraphStore

from ._models import TaskKnowledgeBaseOption
from ._s02_project_planning_evidence import (
    PLANNING_TEMPLATE_NO_MATCH_MESSAGE,
    PLANNING_TEMPLATE_REJECTION_REASON,
    PLANNING_TEMPLATE_SEARCH_QUERIES,
    PROJECT_MATERIAL_SEARCH_QUERIES,
    _planning_template_content_match,
    _planning_template_source_match,
    _project_material_source_match,
)




class TaskKnowledgeError(RuntimeError):
    """A safe, user-facing failure at the Task knowledge boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


class TaskKnowledgeGateway:
    """Adapt the shared AgentScope KB service to the Task execution boundary."""

    _rebuild_locks: dict[str, asyncio.Lock] = {}

    def __init__(self, knowledge_base_service: Any, app_state: Any) -> None:
        self._service = knowledge_base_service
        self._app_state = app_state
        self._extractor: Any | None = None
        self._extractor_initialized = False
        self._graph_store: Any | None = None
        self._graph_store_initialized = False
        self._context_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def list_knowledge_bases(
        self,
        user_id: str,
    ) -> list[TaskKnowledgeBaseOption]:
        """Return visible KBs as a small Task-facing projection."""

        views: list[Any] = []
        page = 1
        while True:
            page_views, total = await self._service.list_knowledge_base_views(
                user_id,
                page=page,
                page_size=128,
            )
            views.extend(page_views)
            if not page_views or len(views) >= total:
                break
            page += 1

        return [
            TaskKnowledgeBaseOption(
                id=view.id,
                name=view.name,
                description=view.description,
                document_count=view.document_count,
                chunk_count=view.chunk_count,
                ready_document_count=view.status_counts.ready,
            )
            for view in views
        ]

    async def retrieve(
        self,
        user_id: str,
        knowledge_base_ids: list[str],
        query: str,
        *,
        top_k: int = 4,
    ) -> str:
        """Retrieve scoped evidence and format it for an Agent step."""

        if not knowledge_base_ids or not query.strip():
            return ""

        options = await self.list_knowledge_bases(user_id)
        names = {option.id: option.name for option in options}
        evidence: list[str] = []
        for knowledge_base_id in knowledge_base_ids:
            results = await self._service.search(
                user_id,
                knowledge_base_id,
                query.strip()[:8000],
                top_k=top_k,
            )
            for result in results:
                content = getattr(getattr(result, "chunk", None), "content", None)
                text = getattr(content, "text", "")
                if not text:
                    continue
                chunk = getattr(result, "chunk", None)
                source = getattr(chunk, "source", "未知来源")
                chunk_index = getattr(chunk, "chunk_index", None)
                citation = source
                if chunk_index is not None:
                    citation = f"{citation} · 第 {chunk_index + 1} 段"
                score = getattr(result, "score", None)
                score_text = f" · 相关度 {score:.2f}" if isinstance(score, float) else ""
                evidence.append(
                    f"[{names.get(knowledge_base_id, knowledge_base_id)} / "
                    f"{citation}{score_text}]\n{text}",
                )

        if not evidence:
            return ""
        return "\n\n".join(evidence[:12])

    async def inspect_task_scope(
        self,
        user_id: str,
        knowledge_base_ids: list[str],
        source_material_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Check selected KB access and indexed material without searching."""

        selected_ids = list(
            dict.fromkeys(item for item in knowledge_base_ids if item)
        )
        if not selected_ids:
            raise TaskKnowledgeError(
                "knowledge_base_required",
                "请先为此 Task 选择当前用户有权访问的知识库。",
            )
        if len(selected_ids) > 16:
            raise TaskKnowledgeError(
                "knowledge_base_limit",
                "单个 Task 最多选择 16 个知识库。",
            )

        try:
            views = await self._resolve_views(user_id, selected_ids)
        except ValueError as error:
            raise TaskKnowledgeError(
                "knowledge_base_unavailable",
                "所选知识库不存在或当前用户无权访问。",
            ) from error
        except Exception as error:  # noqa: BLE001 — fail closed
            raise TaskKnowledgeError(
                "knowledge_service_unavailable",
                "当前知识库服务无法完成 Task 预检。",
            ) from error

        requested_sources = list(
            dict.fromkeys(
                item.strip()
                for item in (source_material_ids or [])
                if isinstance(item, str) and item.strip()
            ),
        )
        if len(requested_sources) > 128:
            raise TaskKnowledgeError(
                "source_material_limit",
                "单个 Task 最多指定 128 份资料。",
            )
        source_records: list[Any] = []
        if requested_sources:
            get_status = getattr(self._service, "get_document_status", None)
            if not callable(get_status):
                raise TaskKnowledgeError(
                    "source_status_unavailable",
                    "当前知识库服务不能确认指定资料的授权和索引状态。",
                )
            try:
                for view in views:
                    source_records.extend(
                        await get_status(user_id, view.id, requested_sources),
                    )
            except Exception as error:  # noqa: BLE001 — fail closed
                raise TaskKnowledgeError(
                    "source_status_unavailable",
                    "当前知识库服务不能确认指定资料的授权和索引状态。",
                ) from error
            found_ids = {
                str(getattr(record, "id", "")) for record in source_records
            }
            missing_ids = sorted(set(requested_sources) - found_ids)
            if missing_ids:
                raise TaskKnowledgeError(
                    "source_material_not_found",
                    "部分资料标识未在当前用户可访问的所选知识库中找到。请核对文档 ID 和知识库选择；Task 不会扩大检索范围。",
                    details={"missing_source_count": len(missing_ids)},
                )
            not_ready = [
                record
                for record in source_records
                if getattr(record, "status", None) != "ready"
            ]
            if not_ready:
                raise TaskKnowledgeError(
                    "source_material_not_ready",
                    "指定资料尚未完成索引，暂时不能用于可核验引用。",
                )
        elif not any(
            int(getattr(getattr(view, "status_counts", None), "ready", 0) or 0)
            for view in views
        ):
            raise TaskKnowledgeError(
                "no_indexed_material",
                "所选知识库中没有已完成索引的资料。",
            )

        return {
            "knowledge_bases": [
                {
                    "id": view.id,
                    "name": view.name,
                    "ready_document_count": int(
                        getattr(
                            getattr(view, "status_counts", None),
                            "ready",
                            0,
                        )
                        or 0
                    ),
                }
                for view in views
            ],
            "source_material_ids": requested_sources,
            "source_records": source_records,
        }

    async def retrieve_task_evidence(
        self,
        user_id: str,
        knowledge_base_ids: list[str],
        query: str,
        *,
        source_material_ids: list[str] | None = None,
        top_k: int = 8,
    ) -> dict[str, Any]:
        """Search authorized KBs and retain evidence for citation checks."""

        normalized_query = query.strip()[:8_000]
        if not normalized_query:
            raise TaskKnowledgeError(
                "query_required",
                "项目策划输入为空，无法检索授权资料。",
            )
        scope = await self.inspect_task_scope(
            user_id,
            knowledge_base_ids,
            source_material_ids,
        )
        try:
            views = await self._resolve_views(
                user_id,
                [item["id"] for item in scope["knowledge_bases"]],
            )
        except ValueError as error:
            raise TaskKnowledgeError(
                "knowledge_base_unavailable",
                "检索期间知识库不可用或当前用户无权访问。",
            ) from error
        except Exception as error:  # noqa: BLE001 — fail closed
            raise TaskKnowledgeError(
                "knowledge_service_unavailable",
                "当前知识库服务无法完成授权检索。",
            ) from error
        requested_sources = set(scope["source_material_ids"])
        evidence: list[dict[str, Any]] = []
        get_status = getattr(self._service, "get_document_status", None)
        if not callable(get_status):
            raise TaskKnowledgeError(
                "source_status_unavailable",
                "当前知识库服务不能确认检索结果的来源状态。",
            )

        try:
            for view in views:
                results = await self._service.search(
                    user_id,
                    view.id,
                    normalized_query,
                    top_k=max(1, min(top_k, 12)),
                )
                result_ids = {
                    str(getattr(result, "document_id", ""))
                    for result in results
                    if getattr(result, "document_id", None)
                }
                if requested_sources:
                    result_ids.intersection_update(requested_sources)
                if not result_ids:
                    continue
                records = await get_status(
                    user_id,
                    view.id,
                    sorted(result_ids),
                )
                records_by_id = {
                    str(getattr(record, "id", "")): record
                    for record in records
                    if getattr(record, "status", None) == "ready"
                }
                for result in results:
                    document_id = str(getattr(result, "document_id", ""))
                    record = records_by_id.get(document_id)
                    if record is None:
                        continue
                    chunk = getattr(result, "chunk", None)
                    content = getattr(chunk, "content", None)
                    raw_text = getattr(content, "text", "")
                    if not isinstance(raw_text, str) or not raw_text.strip():
                        continue
                    chunk_index = getattr(chunk, "chunk_index", None)
                    if (
                        not isinstance(chunk_index, int)
                        or isinstance(chunk_index, bool)
                        or chunk_index < 0
                    ):
                        continue
                    digest = hashlib.sha256(
                        raw_text.encode("utf-8")
                    ).hexdigest()
                    max_chars = 2_000
                    source_text = raw_text[:max_chars]
                    updated_at = getattr(record, "updated_at", None)
                    score = getattr(result, "score", None)
                    source_name = getattr(
                        getattr(record, "data", None),
                        "filename",
                        None,
                    )
                    if (
                        not isinstance(source_name, str)
                        or not source_name.strip()
                    ):
                        source_name = getattr(chunk, "source", None)
                    source_name = (
                        source_name.strip()
                        if isinstance(source_name, str)
                        else ""
                    )
                    evidence.append(
                        {
                            "knowledge_base_id": view.id,
                            "knowledge_base_name": view.name,
                            "document_id": document_id,
                            "source": source_name,
                            "source_updated_at": (
                                updated_at.isoformat()
                                if hasattr(updated_at, "isoformat")
                                else None
                            ),
                            "chunk_index": chunk_index,
                            "total_chunks": getattr(
                                chunk,
                                "total_chunks",
                                None,
                            ),
                            "score": (
                                score
                                if isinstance(score, (int, float))
                                else None
                            ),
                            "content_sha256": digest,
                            "source_text": source_text,
                            "source_text_truncated": len(raw_text) > max_chars,
                            "source_status": "ready",
                        },
                    )
        except TaskKnowledgeError:
            raise
        except Exception as error:  # noqa: BLE001 — report retrieval failure
            raise TaskKnowledgeError(
                "retrieval_failed",
                "授权知识库检索失败，Task 已停止，未生成策划初稿。",
            ) from error

        evidence.sort(
            key=lambda item: float(item["score"] or 0),
            reverse=True,
        )
        evidence = evidence[:6]
        if not evidence:
            raise TaskKnowledgeError(
                "no_matching_evidence",
                "授权资料中没有检索到可引用的原文片段，Task 已停止。",
            )
        return {
            "status": "ready",
            "query": normalized_query,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "knowledge_bases": scope["knowledge_bases"],
            "evidence": evidence,
            "citation_protocol": (
                "策划正文中的事实必须标注 [C1] 等引用编号。末尾添加唯一的 "
                "```task-citations JSON 区块，逐项包含 citation_id、evidence_id 和 quote；"
                "evidence_id 必须取自本结果中的服务端证据记录，quote 必须是对应原文片段的"
                "连续逐字摘录。历史任务仍可读取 knowledge_base_id、document_id、chunk_index、"
                "content_sha256 格式。无法获得证据的内容标为建议或待补充，不得伪造来源。"
            ),
        }

    async def retrieve_project_material_evidence(
        self,
        user_id: str,
        knowledge_base_ids: list[str],
        *,
        project_name: str = "",
        source_material_ids: list[str] | None = None,
        top_k: int = 8,
    ) -> dict[str, Any]:
        """Retrieve project facts separately from the planning template.

        Search terms describe project-material intent and never repeat the
        complete user brief. Search results still pass through the existing
        user-scoped document status and Task knowledge-base checks. A missing
        match is recorded as ``not_found`` so downstream output can mark facts
        pending; authorization or service errors remain fail-closed.
        """

        requested_sources = list(dict.fromkeys(source_material_ids or []))
        scope = await self.inspect_task_scope(
            user_id,
            knowledge_base_ids,
            requested_sources,
        )
        queries = list(PROJECT_MATERIAL_SEARCH_QUERIES)
        normalized_name = project_name.strip()[:120]
        if normalized_name:
            queries[0] = (
                f"{normalized_name} 项目委托 项目概况 项目背景 建设目标"
            )

        candidates_by_key: dict[
            tuple[str, str, int, str], dict[str, Any]
        ] = {}
        query_attempts: list[dict[str, Any]] = []
        hit_documents: dict[tuple[str, str], dict[str, str]] = {}
        rejected_documents: dict[tuple[str, str], dict[str, str]] = {}
        knowledge_bases = scope["knowledge_bases"]

        for query in queries:
            try:
                result = await self.retrieve_task_evidence(
                    user_id,
                    knowledge_base_ids,
                    query,
                    source_material_ids=requested_sources,
                    top_k=top_k,
                )
            except TaskKnowledgeError as error:
                query_attempts.append(
                    {
                        "query": query,
                        "status": "no_evidence"
                        if error.code == "no_matching_evidence"
                        else "failed",
                    },
                )
                if error.code == "no_matching_evidence":
                    continue
                details = {
                    "queries": queries,
                    "query_attempts": query_attempts,
                    "hit_documents": list(hit_documents.values()),
                    "rejected_documents": list(rejected_documents.values()),
                    "failure_reason": error.message,
                }
                raise TaskKnowledgeError(
                    error.code,
                    error.message,
                    details=details,
                ) from error

            knowledge_bases = result["knowledge_bases"]
            query_attempts.append(
                {
                    "query": query,
                    "status": "evidence_found",
                    "evidence_count": len(result["evidence"]),
                },
            )
            for item in result["evidence"]:
                key = (
                    item["knowledge_base_id"],
                    item["document_id"],
                    item["chunk_index"],
                    item["content_sha256"],
                )
                document_key = (item["knowledge_base_id"], item["document_id"])
                source = item.get("source")
                source_name = source.strip() if isinstance(source, str) else ""
                hit_documents.setdefault(
                    document_key,
                    {
                        "knowledge_base_name": item["knowledge_base_name"],
                        "document_id": item["document_id"],
                        "source": source_name,
                    },
                )
                candidates_by_key.setdefault(key, item)

        candidates_by_document: dict[
            tuple[str, str], list[dict[str, Any]]
        ] = {}
        for item in candidates_by_key.values():
            key = (item["knowledge_base_id"], item["document_id"])
            candidates_by_document.setdefault(key, []).append(item)

        evidence_by_key: dict[
            tuple[str, str, int, str], dict[str, Any]
        ] = {}
        for document_key, document_chunks in candidates_by_document.items():
            source_names = {
                item.get("source") for item in document_chunks
            }
            source_name = (
                next(iter(source_names)) if len(source_names) == 1 else ""
            )
            combined_text = "\n".join(
                item["source_text"]
                for item in sorted(
                    document_chunks,
                    key=lambda candidate: candidate["chunk_index"],
                )
            )
            is_selected = document_key[1] in requested_sources
            if len(source_names) != 1 or not _project_material_source_match(
                source_name,
                combined_text,
                selected=is_selected,
            ):
                rejected_documents.setdefault(
                    document_key,
                    {
                        **hit_documents[document_key],
                        "reason": "来源标题或同一文档原文片段不符合项目委托资料特征。",
                    },
                )
                continue
            for item in document_chunks:
                key = (
                    item["knowledge_base_id"],
                    item["document_id"],
                    item["chunk_index"],
                    item["content_sha256"],
                )
                evidence_by_key[key] = {
                    **item,
                    "evidence_type": "project_material",
                }

        evidence = sorted(
            evidence_by_key.values(),
            key=lambda item: (
                item["knowledge_base_name"],
                item["source"],
                item["chunk_index"],
            ),
        )[:18]
        diagnostics = {
            "queries": queries,
            "query_attempts": query_attempts,
            "hit_documents": list(hit_documents.values()),
            "rejected_documents": list(rejected_documents.values()),
            "selected_source_material_ids": requested_sources,
        }
        if not evidence:
            failure_reason = (
                "授权检索未命中项目委托资料。"
                if not hit_documents
                else "检索命中文档未满足项目资料标题和原文特征校验。"
            )
            diagnostics["failure_reason"] = failure_reason
            return {
                "status": "not_found",
                "message": "未检索到匹配的项目委托资料。",
                "drafting_guidance": (
                    "未找到符合条件的授权项目委托资料，不能将用户输入当作知识库证据。"
                    "若必填项目名称和项目背景已由用户提供，可继续形成待核实的策划草稿；"
                    "相关内容须标记为用户陈述、DEMO/SYNTHETIC 演示假设或待核实。"
                    "不得仅因缺少项目资料来源而重复追问；其他确实缺失且会阻止草稿产出的"
                    "必需输入仍须按正常规则请求补充。"
                ),
                "queries": queries,
                "query_attempts": query_attempts,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "knowledge_bases": knowledge_bases,
                "evidence": [],
                "retrieval_diagnostics": diagnostics,
            }

        return {
            "status": "ready",
            "message": "已检索到符合项目资料特征的授权原文。",
            "queries": queries,
            "query_attempts": query_attempts,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "knowledge_bases": knowledge_bases,
            "evidence": evidence,
            "retrieval_diagnostics": diagnostics,
        }

    async def retrieve_planning_template_evidence(
        self,
        user_id: str,
        knowledge_base_ids: list[str],
        *,
        top_k: int = 12,
    ) -> dict[str, Any]:
        """Search selected authorized KBs for a real planning template.

        The four bounded queries represent only the template-retrieval intent.
        Results from different chunks are combined only when they belong to the
        same authorized KB document. Project material IDs belong to a separate
        retrieval intent and do not constrain template lookup. Every result
        still passes the existing per-user document-status check in
        ``retrieve_task_evidence``; no KB outside the Task selection is read.
        """

        query_attempts: list[dict[str, Any]] = []
        evidence_by_key: dict[tuple[str, str, int, str], dict[str, Any]] = {}
        knowledge_bases: list[dict[str, Any]] | None = None
        hit_documents: dict[tuple[str, str], dict[str, str]] = {}
        for query in PLANNING_TEMPLATE_SEARCH_QUERIES:
            try:
                result = await self.retrieve_task_evidence(
                    user_id,
                    knowledge_base_ids,
                    query,
                    top_k=top_k,
                )
            except TaskKnowledgeError as error:
                query_attempts.append(
                    {
                        "query": query,
                        "status": "no_evidence"
                        if error.code == "no_matching_evidence"
                        else "failed",
                    },
                )
                if error.code == "no_matching_evidence":
                    continue
                details = {
                    "queries": list(PLANNING_TEMPLATE_SEARCH_QUERIES),
                    "query_attempts": query_attempts,
                    "hit_documents": list(hit_documents.values()),
                    "failure_reason": error.message,
                }
                raise TaskKnowledgeError(
                    error.code,
                    error.message,
                    details=details,
                ) from error
            if knowledge_bases is None:
                knowledge_bases = result["knowledge_bases"]
            query_attempts.append(
                {
                    "query": query,
                    "status": "evidence_found",
                    "evidence_count": len(result["evidence"]),
                },
            )
            for item in result["evidence"]:
                key = (
                    item["knowledge_base_id"],
                    item["document_id"],
                    item["chunk_index"],
                    item["content_sha256"],
                )
                evidence_by_key.setdefault(key, item)
                document_key = (item["knowledge_base_id"], item["document_id"])
                hit_documents.setdefault(
                    document_key,
                    {
                        "knowledge_base_name": item["knowledge_base_name"],
                        "document_id": item["document_id"],
                        "source": item["source"],
                    },
                )

        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in evidence_by_key.values():
            key = (item["knowledge_base_id"], item["document_id"])
            grouped.setdefault(key, []).append(item)

        matches: list[dict[str, Any]] = []
        rejected_documents: list[dict[str, Any]] = []
        for key, document_evidence in grouped.items():
            source = document_evidence[0]["source"]
            metadata_complete = all(
                isinstance(item.get("knowledge_base_name"), str)
                and bool(item["knowledge_base_name"].strip())
                and isinstance(item.get("document_id"), str)
                and bool(item["document_id"].strip())
                and isinstance(item.get("chunk_index"), int)
                and item["chunk_index"] >= 0
                and isinstance(item.get("content_sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", item["content_sha256"])
                and isinstance(item.get("source"), str)
                and bool(item["source"].strip())
                and item["source"] != "未知来源"
                and isinstance(item.get("source_text"), str)
                and bool(item["source_text"].strip())
                for item in document_evidence
            )
            if not metadata_complete:
                rejected_documents.append(
                    {
                        **hit_documents[key],
                        "reason": "检索原文缺少完整且真实的来源字段。",
                    },
                )
                continue
            if not _planning_template_source_match(source):
                rejected_documents.append(
                    {
                        **hit_documents[key],
                        "reason": "来源文档标题不能确认其为策划报告模板。",
                    },
                )
                continue
            content_matches, content_diagnostics, reason = (
                _planning_template_content_match(document_evidence)
            )
            if not content_matches:
                rejected_documents.append(
                    {
                        **hit_documents[key],
                        "reason": reason,
                        **content_diagnostics,
                    },
                )
                continue
            matches.append(
                {
                    "knowledge_base_name": document_evidence[0][
                        "knowledge_base_name"
                    ],
                    "document_id": document_evidence[0]["document_id"],
                    "source": source,
                    **content_diagnostics,
                    "evidence": sorted(
                        document_evidence,
                        key=lambda item: item["chunk_index"],
                    ),
                },
            )

        diagnostics = {
            "queries": list(PLANNING_TEMPLATE_SEARCH_QUERIES),
            "query_attempts": query_attempts,
            "hit_documents": list(hit_documents.values()),
            "rejected_documents": rejected_documents,
        }
        if not matches:
            failure_reason = (
                "授权检索未返回可追溯原文。"
                if not hit_documents
                else PLANNING_TEMPLATE_REJECTION_REASON
            )
            diagnostics["failure_reason"] = failure_reason
            diagnostic_line = (
                "定向检索词："
                + "；".join(PLANNING_TEMPLATE_SEARCH_QUERIES)
                + "。命中文档："
                + (
                    "、".join(
                        f"{item['source']}（{item['document_id']}）"
                        for item in hit_documents.values()
                    )
                    or "无"
                )
                + f"。失败原因：{failure_reason}"
            )
            raise TaskKnowledgeError(
                "no_matching_template",
                f"{PLANNING_TEMPLATE_NO_MATCH_MESSAGE}\n{diagnostic_line}",
                details=diagnostics,
            )

        selected_evidence = [
            {**item, "evidence_type": "planning_template"}
            for match in matches
            for item in match["evidence"]
        ]
        selected_evidence.sort(
            key=lambda item: (
                item["knowledge_base_name"],
                item["source"],
                item["chunk_index"],
            ),
        )
        return {
            "status": "ready",
            "query": "\n".join(PLANNING_TEMPLATE_SEARCH_QUERIES),
            "queries": list(PLANNING_TEMPLATE_SEARCH_QUERIES),
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "knowledge_bases": knowledge_bases or [],
            "template_matches": [
                {
                    key: value
                    for key, value in match.items()
                    if key != "evidence"
                }
                for match in matches
            ],
            "retrieval_diagnostics": diagnostics,
            "evidence": selected_evidence,
            "citation_protocol": (
                "模板和策划事实只引用本结果保留的授权原文片段。task-citations 使用本结果中"
                "的 evidence_id 和对应的连续逐字原文摘录；"
                "不得以模型总结或自行补充的来源代替原文证据。"
            ),
        }

    @staticmethod
    def format_task_evidence(result: dict[str, Any]) -> str:
        """Serialize retrieval evidence with bounded JSON formatting."""

        return json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )

    async def get_graph(
        self,
        user_id: str,
        knowledge_base_ids: list[str],
        *,
        query: str | None = None,
        node_limit: int = 120,
        edge_limit: int = 240,
    ) -> dict[str, Any]:
        """Read and merge bounded graphs for the selected KBs."""

        views = await self._resolve_views(user_id, knowledge_base_ids)
        if not views:
            return self._empty_graph(knowledge_base_ids)

        merged_nodes: dict[str, dict[str, Any]] = {}
        merged_edges: dict[str, dict[str, Any]] = {}
        statuses: list[str] = []
        errors: list[str] = []
        versions: list[int] = []
        for view in views:
            try:
                graph = await self._read_graph(
                    user_id,
                    view,
                    query=query,
                    node_limit=node_limit,
                    edge_limit=edge_limit,
                )
            except Exception as error:  # noqa: BLE001 — keep other KBs visible
                statuses.append("error")
                errors.append(str(error) or "知识图谱读取失败。")
                continue
            statuses.append(str(graph.get("status", "empty")))
            if graph.get("error"):
                errors.append(str(graph["error"]))
            versions.append(int(graph.get("version", 0) or 0))
            for node in graph.get("nodes", []):
                node_payload = dict(node)
                node_payload.setdefault("properties", {})
                node_payload["properties"] = {
                    **node_payload["properties"],
                    "knowledge_base_id": view.id,
                }
                merged_nodes[node_payload["id"]] = node_payload
            for edge in graph.get("edges", []):
                merged_edges[edge["id"]] = dict(edge)

        nodes = list(merged_nodes.values())[:node_limit]
        node_ids = {node["id"] for node in nodes}
        edges = [
            edge
            for edge in merged_edges.values()
            if edge.get("source") in node_ids and edge.get("target") in node_ids
        ][:edge_limit]
        status = self._merge_statuses(statuses)
        return {
            "knowledge_base_ids": knowledge_base_ids,
            "extraction_enabled": self.extraction_enabled,
            "status": status,
            "error": "; ".join(dict.fromkeys(errors))[:500] or None,
            "nodes": nodes,
            "edges": edges,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "version": max(versions, default=0),
        }

    async def get_context_graph(
        self,
        user_id: str,
        knowledge_base_ids: list[str],
        query: str,
        *,
        node_limit: int = 60,
        edge_limit: int = 120,
        force_extract: bool = False,
    ) -> dict[str, Any]:
        """Build a small graph for a task/step context.

        The fast path reuses the already-built document graph and scopes it
        to documents returned by the existing vector search.  Only when a
        selected knowledge base has no ready graph contribution do we send
        the relevant chunks to the LLM extractor.  This keeps the Task
        feature lightweight without changing AgentScope's shared indexing
        pipeline.
        """

        if not knowledge_base_ids or not query.strip():
            payload = self._empty_graph(knowledge_base_ids)
            payload["mode"] = "empty"
            return payload

        cache_key = "|".join(
            [
                user_id,
                ",".join(sorted(dict.fromkeys(knowledge_base_ids))),
                query.strip()[:8000],
            ],
        )
        if not force_extract:
            cached = self._context_cache.get(cache_key)
            cache_ttl = _env_int(
                "TASK_KNOWLEDGE_GRAPH_CONTEXT_CACHE_SECONDS",
                120,
                0,
                3600,
            )
            if cached is not None and time.monotonic() - cached[0] < cache_ttl:
                return cached[1]

        views = await self._resolve_views(user_id, knowledge_base_ids)
        top_k = _env_int("TASK_KNOWLEDGE_GRAPH_CONTEXT_TOP_K", 8, 1, 32)
        max_chunks = _env_int(
            "TASK_KNOWLEDGE_GRAPH_CONTEXT_MAX_CHUNKS",
            12,
            1,
            48,
        )
        merged: dict[str, Any] = {
            "knowledge_base_ids": knowledge_base_ids,
            "extraction_enabled": self.extraction_enabled,
            "status": "empty",
            "error": None,
            "nodes": [],
            "edges": [],
            "node_count": 0,
            "edge_count": 0,
            "version": 0,
            "mode": "stored",
            "matched_chunk_count": 0,
            "extracted_chunk_count": 0,
        }
        errors: list[str] = []
        modes: set[str] = set()
        statuses: list[str] = []

        for view in views:
            try:
                results = await self._service.search(
                    user_id,
                    view.id,
                    query.strip()[:8000],
                    top_k=top_k,
                )
                selected_chunks = await self._expand_search_results(
                    user_id,
                    view.id,
                    results,
                    max_chunks=max_chunks,
                )
                merged["matched_chunk_count"] += len(selected_chunks)
                document_ids = sorted(
                    {
                        str(getattr(result, "document_id", ""))
                        for result in results
                        if getattr(result, "document_id", None)
                    },
                )
                stored = await self._read_graph(
                    user_id,
                    view,
                    query=None,
                    document_ids=document_ids,
                    node_limit=node_limit,
                    edge_limit=edge_limit,
                )
                stored_status = str(stored.get("status", "empty"))
                statuses.append(stored_status)

                should_extract = (
                    force_extract
                    or stored_status in {"empty", "error", "disabled"}
                )
                if not should_extract or self.extractor is None or not selected_chunks:
                    self._merge_graph_payload(merged, stored, view.id)
                    modes.add("stored")
                    if stored.get("error"):
                        errors.append(str(stored["error"]))
                    continue

                extracted = await self._extract_context_chunks(
                    selected_chunks,
                    filename_by_document={
                        str(result.document_id): getattr(
                            result.chunk,
                            "source",
                            "未知来源",
                        )
                        for result in results
                        if getattr(result, "document_id", None)
                    },
                )
                if extracted:
                    for graph in extracted:
                        self._merge_graph_payload(
                            merged,
                            graph.model_dump(mode="json"),
                            view.id,
                        )
                    merged["extracted_chunk_count"] += len(selected_chunks)
                    modes.add("llm")
                else:
                    self._merge_graph_payload(merged, stored, view.id)
                    modes.add("stored")
            except Exception as error:  # noqa: BLE001 — keep other KBs visible
                errors.append(str(error) or "知识图谱上下文生成失败。")
                statuses.append("error")

        merged["nodes"] = merged["nodes"][:node_limit]
        node_ids = {node["id"] for node in merged["nodes"]}
        merged["edges"] = [
            edge
            for edge in merged["edges"]
            if edge.get("source") in node_ids and edge.get("target") in node_ids
        ][:edge_limit]
        merged["node_count"] = len(merged["nodes"])
        merged["edge_count"] = len(merged["edges"])
        merged["mode"] = "hybrid" if len(modes) > 1 else next(iter(modes), "empty")
        merged["status"] = self._merge_statuses(statuses)
        if merged["nodes"] and merged["status"] == "empty":
            merged["status"] = "ready"
        merged["error"] = "; ".join(dict.fromkeys(errors))[:500] or None
        if not force_extract:
            self._context_cache[cache_key] = (time.monotonic(), merged)
        return merged

    async def rebuild_graph(
        self,
        user_id: str,
        knowledge_base_ids: list[str],
        *,
        force_extract: bool = False,
    ) -> dict[str, Any]:
        """Incrementally extract graph contributions from indexed chunks."""

        views = await self._resolve_views(user_id, knowledge_base_ids)
        if not views:
            return {
                "knowledge_base_ids": knowledge_base_ids,
                "extraction_enabled": self.extraction_enabled,
                "status": "empty",
                "documents": 0,
                "skipped": 0,
                "error": None,
            }

        extractor = self.extractor
        graph_store = self.graph_store
        if extractor is None or graph_store is None:
            return {
                "knowledge_base_ids": knowledge_base_ids,
                "extraction_enabled": False,
                "status": "disabled",
                "documents": 0,
                "skipped": 0,
                "error": "未配置关系抽取模型或图存储。请设置 DEEPSEEK_API_KEY 后重启后端。",
            }

        processed = 0
        skipped = 0
        reused = 0
        errors: list[str] = []
        for view in views:
            lock = self._rebuild_locks.setdefault(view.id, asyncio.Lock())
            async with lock:
                try:
                    if force_extract:
                        await graph_store.delete_knowledge_base_graph(
                            view.owner_id,
                            view.id,
                        )
                    await graph_store.set_status(
                        view.owner_id,
                        view.id,
                        "building",
                    )
                    documents = await self._list_all_documents(
                        user_id,
                        view.id,
                    )
                    for document in documents:
                        document_status = getattr(document.status, "value", document.status)
                        if document_status != "ready" or document.data.chunk_count <= 0:
                            skipped += 1
                            continue
                        chunks = await self._list_all_chunks(
                            user_id,
                            view.id,
                            document.id,
                        )
                        if not chunks:
                            skipped += 1
                            continue
                        if not force_extract:
                            existing = await self._read_graph(
                                user_id,
                                view,
                                query=None,
                                document_ids=[document.id],
                                node_limit=1,
                                edge_limit=1,
                            )
                            if existing.get("nodes"):
                                reused += 1
                                continue
                        graph = await extractor.extract(
                            chunks,
                            document_id=document.id,
                            filename=document.data.filename,
                        )
                        await graph_store.replace_document_graph(
                            view.owner_id,
                            view.id,
                            graph,
                        )
                        processed += 1
                    await graph_store.set_status(
                        view.owner_id,
                        view.id,
                        "ready",
                    )
                except Exception as error:  # noqa: BLE001 — report per-KB failure
                    message = str(error) or "知识图谱构建失败。"
                    errors.append(f"{view.name}: {message}")
                    try:
                        await graph_store.set_status(
                            view.owner_id,
                            view.id,
                            "error",
                            error=message,
                        )
                    except Exception:  # noqa: BLE001 — preserve original error
                        pass

        self._context_cache.clear()

        return {
            "knowledge_base_ids": knowledge_base_ids,
            "extraction_enabled": True,
            "status": "error" if errors else "ready",
            "documents": processed,
            "skipped": skipped,
            "reused": reused,
            "error": "; ".join(errors)[:500] or None,
        }

    @property
    def extraction_enabled(self) -> bool:
        """Whether an LLM extractor is available for graph rebuilds."""

        return self.extractor is not None

    @property
    def extractor(self) -> Any | None:
        """Lazily reuse AgentScope's extractor or build a DeepSeek adapter."""

        if self._extractor_initialized:
            return self._extractor
        self._extractor_initialized = True
        configured = getattr(self._app_state, "knowledge_graph_extractor", None)
        if configured is not None:
            self._extractor = configured
            return self._extractor

        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            return None
        try:
            from agentscope.model import DeepSeekChatModel
            from agentscope.rag import KnowledgeGraphExtractor
            from agentscope.credential import DeepSeekCredential

            credential = DeepSeekCredential(
                api_key=SecretStr(api_key),
                base_url=os.getenv(
                    "DEEPSEEK_BASE_URL",
                    "https://api.deepseek.com",
                ),
            )
            model = DeepSeekChatModel(
                credential=credential,
                model=(
                    os.getenv("DEEPSEEK_CHAT_MODEL")
                    or os.getenv("DEEPSEEK_MODEL")
                    or "deepseek-chat"
                ),
                parameters=DeepSeekChatModel.Parameters(
                    temperature=0,
                    max_tokens=2048,
                ),
                stream=False,
            )
            self._extractor = KnowledgeGraphExtractor(
                model=model,
                max_concurrency=max(
                    1,
                    int(os.getenv("TASK_KNOWLEDGE_GRAPH_CONCURRENCY", "2")),
                ),
            )
        except Exception:
            # Configuration errors remain an explicit disabled state; the
            # Task execution path must still work without a graph model.
            self._extractor = None
        return self._extractor

    @property
    def graph_store(self) -> Any | None:
        """Reuse the app store, or bind the standard Redis graph adapter."""

        if self._graph_store_initialized:
            return self._graph_store
        self._graph_store_initialized = True
        configured = getattr(self._app_state, "knowledge_graph_store", None)
        if configured is not None:
            self._graph_store = configured
            return self._graph_store
        storage = getattr(self._app_state, "storage", None)
        if storage is not None and callable(getattr(storage, "get_client", None)):
            self._graph_store = RedisKnowledgeGraphStore(storage)
        return self._graph_store

    async def _expand_search_results(
        self,
        user_id: str,
        knowledge_base_id: str,
        results: list[Any],
        *,
        max_chunks: int,
    ) -> list[tuple[str, Any]]:
        """Keep relevant hits and one neighbouring chunk for continuity."""

        direct: dict[tuple[str, int], Any] = {}
        hit_indices: dict[str, set[int]] = {}
        for result in results:
            document_id = getattr(result, "document_id", None)
            chunk = getattr(result, "chunk", None)
            chunk_index = getattr(chunk, "chunk_index", None)
            if not document_id or chunk is None or chunk_index is None:
                continue
            key = (str(document_id), int(chunk_index))
            direct[key] = chunk
            hit_indices.setdefault(str(document_id), set()).add(int(chunk_index))

        selected: dict[tuple[str, int], Any] = dict(direct)
        for document_id, indices in hit_indices.items():
            if len(selected) >= max_chunks:
                break
            try:
                all_chunks = await self._list_all_chunks(
                    user_id,
                    knowledge_base_id,
                    document_id,
                )
            except Exception:  # noqa: BLE001 — direct hits remain usable
                continue
            by_index = {
                int(getattr(chunk, "chunk_index", -1)): chunk
                for chunk in all_chunks
            }
            neighbours = sorted(
                {
                    candidate
                    for index in indices
                    for candidate in (index - 1, index, index + 1)
                    if candidate >= 0
                },
                key=lambda value: (min(abs(value - index) for index in indices), value),
            )
            for chunk_index in neighbours:
                chunk = by_index.get(chunk_index)
                if chunk is None:
                    continue
                selected.setdefault((document_id, chunk_index), chunk)
                if len(selected) >= max_chunks:
                    break

        return list(selected.items())[:max_chunks]

    async def _extract_context_chunks(
        self,
        selected_chunks: list[tuple[str, Any]],
        *,
        filename_by_document: dict[str, str],
    ) -> list[Any]:
        """Run the existing extractor only for selected context chunks."""

        if self.extractor is None:
            return []
        grouped: dict[str, list[Any]] = {}
        for document_id, chunk in selected_chunks:
            grouped.setdefault(document_id, []).append(chunk)
        graphs: list[Any] = []
        for document_id, chunks in grouped.items():
            graphs.append(
                await self.extractor.extract(
                    chunks,
                    document_id=document_id,
                    filename=filename_by_document.get(document_id, "未知来源"),
                ),
            )
        return graphs

    @staticmethod
    def _merge_graph_payload(
        target: dict[str, Any],
        payload: dict[str, Any],
        knowledge_base_id: str,
    ) -> None:
        """Merge stored or ephemeral graph payloads at the Task boundary."""

        nodes_by_id = {node["id"]: node for node in target["nodes"]}
        for raw_node in payload.get("nodes", []):
            node = dict(raw_node)
            node_id = str(node.get("id", ""))
            if not node_id:
                continue
            node["properties"] = {
                **dict(node.get("properties") or {}),
                "knowledge_base_id": knowledge_base_id,
            }
            existing = nodes_by_id.get(node_id)
            if existing is None:
                nodes_by_id[node_id] = node
                continue
            existing["aliases"] = sorted(
                set(existing.get("aliases") or []).union(node.get("aliases") or []),
            )
            existing["properties"] = {
                **dict(existing.get("properties") or {}),
                **node["properties"],
            }
            existing_refs = existing.setdefault("source_refs", [])
            for source in node.get("source_refs", []):
                if source not in existing_refs:
                    existing_refs.append(source)

        edges_by_id = {edge["id"]: edge for edge in target["edges"]}
        for raw_edge in payload.get("edges", []):
            edge = dict(raw_edge)
            edge_id = str(edge.get("id", ""))
            if not edge_id:
                continue
            existing = edges_by_id.get(edge_id)
            if existing is None:
                edges_by_id[edge_id] = edge
                continue
            existing["properties"] = {
                **dict(existing.get("properties") or {}),
                **dict(edge.get("properties") or {}),
            }
            existing_refs = existing.setdefault("source_refs", [])
            for source in edge.get("source_refs", []):
                if source not in existing_refs:
                    existing_refs.append(source)

        target["nodes"] = list(nodes_by_id.values())
        target["edges"] = list(edges_by_id.values())
        target["version"] = max(
            int(target.get("version", 0) or 0),
            int(payload.get("version", 0) or 0),
        )

    async def _resolve_views(self, user_id: str, ids: list[str]) -> list[Any]:
        if not ids:
            return []
        views: list[Any] = []
        for knowledge_base_id in dict.fromkeys(ids):
            page_views, _ = await self._service.list_knowledge_base_views(
                user_id,
                knowledge_base_id=knowledge_base_id,
                page=1,
                page_size=1,
            )
            if not page_views:
                raise ValueError(f"知识库 '{knowledge_base_id}' 不可用或无权访问。")
            views.append(page_views[0])
        return views

    async def _read_graph(
        self,
        user_id: str,
        view: Any,
        *,
        query: str | None,
        document_ids: list[str] | None = None,
        node_limit: int,
        edge_limit: int,
    ) -> dict[str, Any]:
        configured = getattr(self._app_state, "knowledge_graph_store", None)
        if configured is not None:
            return await self._service.get_knowledge_graph(
                user_id,
                view.id,
                query=query,
                document_ids=document_ids,
                node_limit=node_limit,
                edge_limit=edge_limit,
            )
        graph_store = self.graph_store
        if graph_store is None:
            return self._empty_graph([view.id])
        return await graph_store.get_graph(
            view.owner_id,
            view.id,
            query=query,
            document_ids=document_ids,
            node_limit=node_limit,
            edge_limit=edge_limit,
        )

    async def _list_all_documents(self, user_id: str, knowledge_base_id: str) -> list[Any]:
        documents: list[Any] = []
        page = 1
        while True:
            page_documents, total = await self._service.list_documents(
                user_id,
                knowledge_base_id,
                page=page,
                page_size=128,
            )
            documents.extend(page_documents)
            if not page_documents or len(documents) >= total:
                return documents
            page += 1

    async def _list_all_chunks(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> list[Any]:
        chunks: list[Any] = []
        page = 1
        while True:
            page_chunks, total = await self._service.list_document_chunks(
                user_id,
                knowledge_base_id,
                document_id,
                page=page,
                page_size=128,
            )
            chunks.extend(page_chunks)
            if not page_chunks or len(chunks) >= total:
                return chunks
            page += 1

    @staticmethod
    def _merge_statuses(statuses: list[str]) -> str:
        if not statuses:
            return "empty"
        if "error" in statuses:
            return "error"
        if "building" in statuses:
            return "building"
        if "ready" in statuses:
            return "ready"
        if "disabled" in statuses:
            return "disabled"
        return "empty"

    @staticmethod
    def _empty_graph(knowledge_base_ids: list[str]) -> dict[str, Any]:
        return {
            "knowledge_base_ids": knowledge_base_ids,
            "extraction_enabled": False,
            "status": "empty",
            "error": None,
            "nodes": [],
            "edges": [],
            "node_count": 0,
            "edge_count": 0,
            "version": 0,
            "mode": "empty",
            "matched_chunk_count": 0,
            "extracted_chunk_count": 0,
        }


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read a bounded integer without making Task startup fragile."""

    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))

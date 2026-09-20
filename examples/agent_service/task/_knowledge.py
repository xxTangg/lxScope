# -*- coding: utf-8 -*-
"""Task-owned adapter for AgentScope knowledge bases.

This module is deliberately a boundary, not a second knowledge-base
implementation.  It consumes the public ``KnowledgeBaseService`` methods,
keeps Task-specific selection/retrieval semantics local to the Task module,
and uses AgentScope's graph models/store for entity-relation extraction.
"""

import asyncio
import os
import time
from typing import Any

from pydantic import SecretStr

from agentscope.app.storage import RedisKnowledgeGraphStore

from ._models import TaskKnowledgeBaseOption


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

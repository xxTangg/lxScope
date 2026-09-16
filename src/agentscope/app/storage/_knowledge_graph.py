# -*- coding: utf-8 -*-
"""Knowledge-base graph persistence for the Redis-backed service.

The implementation stores a contribution per document and rebuilds the
knowledge-base view from those contributions.  Rebuilding keeps retries and
document deletion deterministic, while the document-level keys preserve
provenance for every merged node and edge.
"""
import asyncio
from datetime import datetime
from typing import Any, TYPE_CHECKING

from ...rag import DocumentGraph, GraphEdge, GraphNode, KnowledgeGraph

if TYPE_CHECKING:
    from ._redis_storage import RedisStorage


class KnowledgeGraphStoreBase:
    """Small storage contract used by the worker and HTTP service."""

    async def replace_document_graph(
        self,
        user_id: str,
        knowledge_base_id: str,
        graph: DocumentGraph,
    ) -> None:
        raise NotImplementedError

    async def delete_document_graph(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        raise NotImplementedError

    async def delete_knowledge_base_graph(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> None:
        raise NotImplementedError

    async def get_graph(
        self,
        user_id: str,
        knowledge_base_id: str,
        *,
        query: str | None = None,
        document_ids: list[str] | None = None,
        node_limit: int = 300,
        edge_limit: int = 600,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def set_status(
        self,
        user_id: str,
        knowledge_base_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        raise NotImplementedError


class RedisKnowledgeGraphStore(KnowledgeGraphStoreBase):
    """Persist merged graphs using the app's existing Redis instance."""

    def __init__(
        self,
        storage: "RedisStorage",
        key_prefix: str = "agentscope",
    ) -> None:
        self._storage = storage
        self._prefix = key_prefix
        self._locks: dict[str, asyncio.Lock] = {}

    def _client(self) -> Any:
        client = self._storage.get_client()
        if client is None:
            raise RuntimeError("Redis storage is not connected.")
        return client

    def _document_key(self, user_id: str, kb_id: str, doc_id: str) -> str:
        return (
            f"{self._prefix}:knowledge_graph:{user_id}:{kb_id}"
            f":document:{doc_id}"
        )

    def _document_index_key(self, user_id: str, kb_id: str) -> str:
        return f"{self._prefix}:knowledge_graph:{user_id}:{kb_id}:documents"

    def _knowledge_base_key(self, user_id: str, kb_id: str) -> str:
        return f"{self._prefix}:knowledge_graph:{user_id}:{kb_id}:merged"

    def _status_key(self, user_id: str, kb_id: str) -> str:
        return f"{self._prefix}:knowledge_graph:{user_id}:{kb_id}:status"

    def _lock_for(self, user_id: str, kb_id: str) -> asyncio.Lock:
        key = f"{user_id}:{kb_id}"
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def replace_document_graph(
        self,
        user_id: str,
        knowledge_base_id: str,
        graph: DocumentGraph,
    ) -> None:
        """Replace one document contribution and rebuild the merged view."""
        client = self._client()
        async with self._lock_for(user_id, knowledge_base_id):
            await client.set(
                self._document_key(
                    user_id,
                    knowledge_base_id,
                    graph.document_id,
                ),
                graph.model_dump_json(),
            )
            await client.sadd(
                self._document_index_key(user_id, knowledge_base_id),
                graph.document_id,
            )
            await self._rebuild_locked(user_id, knowledge_base_id)
            await self.set_status(user_id, knowledge_base_id, "ready")

    async def set_status(
        self,
        user_id: str,
        knowledge_base_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        """Set the knowledge-base graph build status."""
        import json

        payload: dict[str, str] = {"status": status}
        if error:
            payload["error"] = error[:500]
        await self._client().set(
            self._status_key(user_id, knowledge_base_id),
            json.dumps(payload, ensure_ascii=False),
        )

    async def delete_document_graph(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        """Remove one document's facts and rebuild orphan cleanup."""
        client = self._client()
        async with self._lock_for(user_id, knowledge_base_id):
            await client.delete(
                self._document_key(user_id, knowledge_base_id, document_id),
            )
            await client.srem(
                self._document_index_key(user_id, knowledge_base_id),
                document_id,
            )
            await self._rebuild_locked(user_id, knowledge_base_id)

    async def delete_knowledge_base_graph(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> None:
        """Delete all graph keys belonging to one knowledge base."""
        client = self._client()
        async with self._lock_for(user_id, knowledge_base_id):
            index_key = self._document_index_key(user_id, knowledge_base_id)
            document_ids = await client.smembers(index_key)
            keys = [
                self._document_key(user_id, knowledge_base_id, document_id)
                for document_id in document_ids
            ]
            keys.extend(
                [
                    index_key,
                    self._knowledge_base_key(user_id, knowledge_base_id),
                    self._status_key(user_id, knowledge_base_id),
                ],
            )
            if keys:
                await client.delete(*keys)

    async def get_graph(
        self,
        user_id: str,
        knowledge_base_id: str,
        *,
        query: str | None = None,
        document_ids: list[str] | None = None,
        node_limit: int = 300,
        edge_limit: int = 600,
    ) -> dict[str, Any]:
        """Return a bounded graph payload suitable for the UI."""
        client = self._client()
        raw = await client.get(
            self._knowledge_base_key(user_id, knowledge_base_id),
        )
        status_raw = await client.get(
            self._status_key(user_id, knowledge_base_id),
        )
        status = "empty"
        status_error = None
        if status_raw:
            try:
                import json

                status_payload = json.loads(status_raw)
                status = status_payload.get("status", "empty")
                status_error = status_payload.get("error")
            except (TypeError, ValueError):
                status = status_raw
        if not raw:
            return {
                "status": status,
                "error": status_error,
                "nodes": [],
                "edges": [],
                "node_count": 0,
                "edge_count": 0,
                "version": 0,
            }

        graph = KnowledgeGraph.model_validate_json(raw)
        allowed_documents = set(document_ids or [])
        needle = query.strip().casefold() if query else None

        nodes = graph.nodes
        if allowed_documents:
            nodes = [
                node
                for node in nodes
                if any(
                    ref.document_id in allowed_documents
                    for ref in node.source_refs
                )
            ]
        if needle:
            nodes = [
                node
                for node in nodes
                if needle in node.label.casefold()
                or any(needle in alias.casefold() for alias in node.aliases)
                or needle in node.type.casefold()
            ]

        node_ids = {node.id for node in nodes[:node_limit]}
        edges = [
            edge
            for edge in graph.edges
            if edge.source in node_ids and edge.target in node_ids
        ]
        if allowed_documents:
            edges = [
                edge
                for edge in edges
                if any(
                    ref.document_id in allowed_documents
                    for ref in edge.source_refs
                )
            ]
        return {
            "status": status or "ready",
            "error": status_error,
            "nodes": [
                node.model_dump(mode="json") for node in nodes[:node_limit]
            ],
            "edges": [
                edge.model_dump(mode="json") for edge in edges[:edge_limit]
            ],
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "version": graph.version,
            "updated_at": graph.updated_at,
        }

    async def _rebuild_locked(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> None:
        """Rebuild the materialized KB graph; caller holds the local lock."""
        client = self._client()
        document_ids = await client.smembers(
            self._document_index_key(user_id, knowledge_base_id),
        )
        nodes: dict[str, GraphNode] = {}
        edges: dict[str, GraphEdge] = {}
        for document_id in document_ids:
            raw = await client.get(
                self._document_key(user_id, knowledge_base_id, document_id),
            )
            if not raw:
                continue
            document_graph = DocumentGraph.model_validate_json(raw)
            for node in document_graph.nodes:
                existing = nodes.get(node.id)
                if existing is None:
                    nodes[node.id] = node
                    continue
                existing.aliases = sorted(
                    set(existing.aliases).union(node.aliases),
                )
                existing.properties.update(node.properties)
                existing.source_refs = _merge_refs(
                    existing.source_refs,
                    node.source_refs,
                )
            for edge in document_graph.edges:
                existing_edge = edges.get(edge.id)
                if existing_edge is None:
                    edges[edge.id] = edge
                    continue
                existing_edge.properties.update(edge.properties)
                existing_edge.source_refs = _merge_refs(
                    existing_edge.source_refs,
                    edge.source_refs,
                )

        previous_raw = await client.get(
            self._knowledge_base_key(user_id, knowledge_base_id),
        )
        previous_version = 0
        if previous_raw:
            try:
                previous_version = KnowledgeGraph.model_validate_json(
                    previous_raw,
                ).version
            except ValueError:
                previous_version = 0
        graph = KnowledgeGraph(
            nodes=list(nodes.values()),
            edges=list(edges.values()),
            version=previous_version + 1,
            updated_at=datetime.now().isoformat(),
        )
        if not graph.nodes and not graph.edges:
            await client.delete(
                self._knowledge_base_key(user_id, knowledge_base_id),
            )
            await self.set_status(user_id, knowledge_base_id, "empty")
            return
        await client.set(
            self._knowledge_base_key(user_id, knowledge_base_id),
            graph.model_dump_json(),
        )


def _merge_refs(left: list[Any], right: list[Any]) -> list[Any]:
    """Merge provenance refs without duplicate entries."""
    result = list(left)
    seen = {ref.model_dump_json() for ref in result}
    for ref in right:
        marker = ref.model_dump_json()
        if marker not in seen:
            result.append(ref)
            seen.add(marker)
    return result


__all__ = ["KnowledgeGraphStoreBase", "RedisKnowledgeGraphStore"]

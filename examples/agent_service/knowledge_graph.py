"""Application-owned, on-demand knowledge-base graph feature.

This module intentionally depends only on the public knowledge-base service.
It is not part of AgentScope's indexing, lifecycle, or storage contracts.
"""
import asyncio
import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, SecretStr

from agentscope.app.deps import get_current_user_id
from agentscope.credential import DeepSeekCredential
from agentscope.message import UserMsg
from agentscope.model import DeepSeekChatModel


class GraphSourceRef(BaseModel):
    document_id: str
    chunk_index: int | None = None
    filename: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphNode(BaseModel):
    id: str
    label: str
    type: str = "entity"
    properties: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    source_refs: list[GraphSourceRef] = Field(default_factory=list)


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str
    properties: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[GraphSourceRef] = Field(default_factory=list)


class DocumentGraph(BaseModel):
    document_id: str
    filename: str | None = None
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class _Entity(BaseModel):
    name: str
    type: str = "entity"
    aliases: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class _Relation(BaseModel):
    source: str
    target: str
    label: str
    properties: dict[str, Any] = Field(default_factory=dict)


class _Extracted(BaseModel):
    entities: list[_Entity] = Field(default_factory=list)
    relations: list[_Relation] = Field(default_factory=list)


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"[\s\u3000\-_/.,，。:：;；()（）\[\]【】]+", "", value)


def _id(prefix: str, *values: str) -> str:
    digest = hashlib.sha1("|".join(_normalise(v) for v in values).encode()).hexdigest()[:20]
    return f"{prefix}:{digest}"


class KnowledgeGraphExtractor:
    """Small application-only structured extractor; never called by indexing."""
    def __init__(self, model: Any | None = None, max_concurrency: int = 2) -> None:
        self.model = model
        self._sem = asyncio.Semaphore(max(1, max_concurrency))

    async def extract(self, chunks: list[Any], document_id: str, filename: str) -> DocumentGraph:
        if self.model is None:
            return DocumentGraph(document_id=document_id, filename=filename)
        nodes: dict[str, GraphNode] = {}
        edges: dict[str, GraphEdge] = {}
        names: dict[str, str] = {}
        for chunk in chunks:
            text = str(getattr(getattr(chunk, "content", None), "text", ""))[:12000]
            if not text:
                continue
            async with self._sem:
                response = await self.model.generate_structured_output(
                    [UserMsg(name="knowledge-graph", content=(
                        "从文档片段抽取实体及其关系，只返回结构化结果；"
                        "不要猜测，关系必须连接已抽取实体。\n\n" + text
                    ))], _Extracted)
            extracted = _Extracted.model_validate(response.content)
            ref = GraphSourceRef(document_id=document_id, chunk_index=getattr(chunk, "chunk_index", None), filename=filename, metadata=dict(getattr(chunk, "metadata", {}) or {}))
            relations: list[_Relation] = []
            for entity in extracted.entities:
                if not entity.name.strip():
                    continue
                node_id = _id("entity", entity.type or "entity", entity.name)
                names[_normalise(entity.name)] = node_id
                for alias in entity.aliases:
                    names[_normalise(alias)] = node_id
                node = nodes.setdefault(node_id, GraphNode(id=node_id, label=entity.name.strip(), type=entity.type.strip() or "entity"))
                node.aliases = sorted(set(node.aliases).union(entity.aliases))
                node.properties.update(entity.properties)
                if ref not in node.source_refs:
                    node.source_refs.append(ref)
                relations.extend(extracted.relations)
            for relation in relations:
                source, target = names.get(_normalise(relation.source)), names.get(_normalise(relation.target))
                if not source or not target or not relation.label.strip():
                    continue
                edge_id = _id("edge", source, relation.label, target)
                edge = edges.setdefault(edge_id, GraphEdge(id=edge_id, source=source, target=target, label=relation.label.strip()))
                edge.properties.update(relation.properties)
                if ref not in edge.source_refs:
                    edge.source_refs.append(ref)
        return DocumentGraph(document_id=document_id, filename=filename, nodes=list(nodes.values()), edges=list(edges.values()))


class RedisKnowledgeGraphStore:
    """Application Redis adapter with an isolated lxscope key namespace."""
    def __init__(self, storage: Any) -> None:
        self.storage = storage

    def _key(self, user: str, kb: str, kind: str) -> str:
        return f"lxscope:kb_graph:{user}:{kb}:{kind}"

    def _client(self) -> Any:
        client = self.storage.get_client()
        if client is None:
            raise RuntimeError("Redis storage is not connected.")
        return client

    async def settings(self, user: str, kb: str) -> dict[str, Any]:
        raw = await self._client().get(self._key(user, kb, "settings"))
        return json.loads(raw) if raw else {"enabled": False}

    async def set_enabled(self, user: str, kb: str, enabled: bool) -> dict[str, Any]:
        data = await self.settings(user, kb)
        data["enabled"] = enabled
        await self._client().set(self._key(user, kb, "settings"), json.dumps(data))
        return data

    async def get_graph(self, user: str, kb: str, query: str | None = None, node_limit: int = 300, edge_limit: int = 600, document_ids: list[str] | None = None) -> dict[str, Any]:
        settings = await self.settings(user, kb)
        if not settings.get("enabled"):
            return {"enabled": False, "status": "disabled", "nodes": [], "edges": [], "node_count": 0, "edge_count": 0, "version": 0, "error": None}
        status_raw = await self._client().get(self._key(user, kb, "status"))
        status_payload = json.loads(status_raw) if status_raw else {"status": "empty", "error": None}
        raw = await self._client().get(self._key(user, kb, "graph"))
        if not raw:
            return {"enabled": True, "status": status_payload.get("status", "empty"), "nodes": [], "edges": [], "node_count": 0, "edge_count": 0, "version": 0, "error": status_payload.get("error")}
        graph = json.loads(raw)
        needle = query.casefold().strip() if query else ""
        nodes = [n for n in graph["nodes"] if (not document_ids or any(ref.get("document_id") in document_ids for ref in n.get("source_refs", []))) and (not needle or needle in n["label"].casefold() or needle in n["type"].casefold())]
        by_id = {node["id"]: node for node in nodes}
        # Select relationships first. Taking the first arbitrary nodes and
        # filtering edges afterwards commonly returns an unconnected graph.
        # A bounded connected subgraph paints faster and is meaningful on the
        # first preview.
        edges: list[dict[str, Any]] = []
        ids: set[str] = set()
        for edge in graph["edges"]:
            if edge["source"] not in by_id or edge["target"] not in by_id:
                continue
            proposed = ids | {edge["source"], edge["target"]}
            if len(proposed) > node_limit:
                continue
            edges.append(edge)
            ids = proposed
            if len(edges) >= edge_limit:
                break
        for node in nodes:
            if len(ids) >= node_limit:
                break
            ids.add(node["id"])
        nodes = [by_id[node_id] for node_id in ids]
        return {**graph, "enabled": True, "status": status_payload.get("status", graph.get("status", "ready")), "error": status_payload.get("error"), "nodes": nodes, "edges": edges, "node_count": len(graph["nodes"]), "edge_count": len(graph["edges"])}

    async def set_status(self, user: str, kb: str, status: str, error: str | None = None) -> None:
        await self._client().set(self._key(user, kb, "status"), json.dumps({"status": status, "error": error}))

    async def replace_document_graph(self, user: str, kb: str, graph: DocumentGraph) -> None:
        await self._client().set(self._key(user, kb, f"document:{graph.document_id}"), graph.model_dump_json())

    async def delete_document_graph(self, user: str, kb: str, document_id: str) -> None:
        await self._client().delete(self._key(user, kb, f"document:{document_id}"))

    async def delete_knowledge_base_graph(self, user: str, kb: str) -> None:
        await self.delete(user, kb)

    async def save(self, user: str, kb: str, graphs: list[DocumentGraph]) -> dict[str, Any]:
        nodes: dict[str, GraphNode] = {}; edges: dict[str, GraphEdge] = {}
        for graph in graphs:
            for node in graph.nodes:
                old = nodes.get(node.id)
                if old is None: nodes[node.id] = node
                else:
                    old.aliases = sorted(set(old.aliases).union(node.aliases)); old.properties.update(node.properties); old.source_refs.extend(r for r in node.source_refs if r not in old.source_refs)
            for edge in graph.edges:
                old = edges.get(edge.id)
                if old is None: edges[edge.id] = edge
                else:
                    old.properties.update(edge.properties); old.source_refs.extend(r for r in edge.source_refs if r not in old.source_refs)
        previous = await self._client().get(self._key(user, kb, "graph"))
        version = (json.loads(previous).get("version", 0) if previous else 0) + 1
        data = {"status": "ready", "error": None, "nodes": [n.model_dump(mode="json") for n in nodes.values()], "edges": [e.model_dump(mode="json") for e in edges.values()], "version": version, "updated_at": datetime.now().isoformat()}
        await self._client().set(self._key(user, kb, "graph"), json.dumps(data, ensure_ascii=False))
        await self.set_status(user, kb, "ready")
        return data

    async def delete(self, user: str, kb: str) -> None:
        await self._client().delete(self._key(user, kb, "graph"), self._key(user, kb, "settings"))


class KnowledgeBaseGraphService:
    def __init__(self, knowledge_base_service: Any, storage: Any) -> None:
        self.kb = knowledge_base_service; self.store = RedisKnowledgeGraphStore(storage); self._locks: dict[str, asyncio.Lock] = {}

    async def _view(self, user: str, kb: str) -> Any:
        views, _ = await self.kb.list_knowledge_base_views(user, knowledge_base_id=kb, page=1, page_size=1)
        if not views: raise HTTPException(status_code=404, detail="知识库不存在或无权访问。")
        return views[0]

    def _extractor(self) -> KnowledgeGraphExtractor | None:
        key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not key: return None
        return KnowledgeGraphExtractor(DeepSeekChatModel(credential=DeepSeekCredential(api_key=SecretStr(key), base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")), model=os.getenv("DEEPSEEK_CHAT_MODEL", "deepseek-chat"), parameters=DeepSeekChatModel.Parameters(temperature=0, max_tokens=2048), stream=False))

    async def graph(self, user: str, kb: str, **kwargs: Any) -> dict[str, Any]:
        await self._view(user, kb); return await self.store.get_graph(user, kb, **kwargs)

    async def enable(self, user: str, kb: str, enabled: bool) -> dict[str, Any]:
        await self._view(user, kb); data = await self.store.set_enabled(user, kb, enabled); return {"enabled": data["enabled"]}

    async def generate(self, user: str, kb: str, force: bool = False) -> dict[str, Any]:
        view = await self._view(user, kb)
        settings = await self.store.settings(user, kb)
        if not settings.get("enabled"): raise HTTPException(status_code=409, detail="请先启用知识图谱。")
        extractor = self._extractor()
        if extractor is None: return {"status": "disabled", "error": "未配置关系抽取模型。"}
        lock = self._locks.setdefault(kb, asyncio.Lock())
        async with lock:
            documents, _ = await self.kb.list_documents(user, kb, page=1, page_size=1000)
            graphs: list[DocumentGraph] = []
            for document in documents:
                if str(getattr(document.status, "value", document.status)) != "ready": continue
                chunks, _ = await self.kb.list_document_chunks(user, kb, document.id, page=1, page_size=1000)
                if chunks: graphs.append(await extractor.extract(chunks, document.id, document.data.filename))
            result = await self.store.save(user, kb, graphs)
            return {"status": result["status"], "documents": len(graphs), "error": None}

    async def generate_in_background(self, user: str, kb: str, force: bool) -> None:
        try:
            await self.generate(user, kb, force)
        except Exception as error:  # noqa: BLE001 -- report background failure to preview UI
            await self.store.set_status(user, kb, "error", str(error)[:500])


class GraphSettingsRequest(BaseModel): enabled: bool
class GenerateRequest(BaseModel): force: bool = False

knowledge_graph_router = APIRouter(prefix="/knowledge_bases", tags=["knowledge-graph"])

def _service(request: Request) -> KnowledgeBaseGraphService:
    cached = getattr(request.app.state, "knowledge_base_graph_service", None)
    if cached: return cached
    kb = getattr(request.app.state, "knowledge_base_service", None); storage = getattr(request.app.state, "storage", None)
    if kb is None or storage is None: raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="知识图谱服务未配置。")
    service = KnowledgeBaseGraphService(kb, storage); request.app.state.knowledge_base_graph_service = service; return service

@knowledge_graph_router.get("/{knowledge_base_id}/graph")
async def get_graph(knowledge_base_id: str, request: Request, query: str | None = None, node_limit: int = Query(120, ge=1, le=1000), edge_limit: int = Query(240, ge=1, le=2000), user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    return await _service(request).graph(user_id, knowledge_base_id, query=query, node_limit=node_limit, edge_limit=edge_limit)

@knowledge_graph_router.put("/{knowledge_base_id}/graph/settings")
async def update_settings(knowledge_base_id: str, body: GraphSettingsRequest, request: Request, user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    return await _service(request).enable(user_id, knowledge_base_id, body.enabled)

@knowledge_graph_router.get("/{knowledge_base_id}/graph/settings")
async def get_settings(knowledge_base_id: str, request: Request, user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    service = _service(request)
    await service._view(user_id, knowledge_base_id)
    return await service.store.settings(user_id, knowledge_base_id)

@knowledge_graph_router.post("/{knowledge_base_id}/graph/generate")
async def generate_graph(knowledge_base_id: str, body: GenerateRequest, request: Request, user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    service = _service(request)
    await service._view(user_id, knowledge_base_id)
    settings = await service.store.settings(user_id, knowledge_base_id)
    if not settings.get("enabled"):
        raise HTTPException(status_code=409, detail="请先启用知识图谱。")
    await service.store.set_status(user_id, knowledge_base_id, "building")
    asyncio.create_task(
        service.generate_in_background(user_id, knowledge_base_id, body.force),
        name=f"knowledge-graph:{knowledge_base_id}",
    )
    return {"status": "building", "documents": 0, "error": None}

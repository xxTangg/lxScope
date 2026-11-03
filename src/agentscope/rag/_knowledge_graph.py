# -*- coding: utf-8 -*-
"""Knowledge-graph data models and LLM-backed extraction helpers.

The graph models deliberately keep document/chunk provenance on every
node and edge.  This lets a knowledge-base graph merge facts from many
documents while still allowing the UI to navigate back to the source.
"""
import asyncio
import hashlib
import re
import unicodedata
from typing import Any, TYPE_CHECKING

from pydantic import BaseModel, Field

from ..message import UserMsg

if TYPE_CHECKING:
    from ..model import ChatModelBase
    from ._document import Chunk


class GraphSourceRef(BaseModel):
    """A source location supporting one graph fact."""

    document_id: str
    chunk_index: int | None = None
    filename: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphNode(BaseModel):
    """A canonical entity in a knowledge-base graph."""

    id: str
    label: str
    type: str = "entity"
    properties: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    source_refs: list[GraphSourceRef] = Field(default_factory=list)


class GraphEdge(BaseModel):
    """A directed relation between two canonical entities."""

    id: str
    source: str
    target: str
    label: str
    properties: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[GraphSourceRef] = Field(default_factory=list)


class KnowledgeGraph(BaseModel):
    """A merged graph for one knowledge base."""

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    version: int = 0
    updated_at: str | None = None


class DocumentGraph(KnowledgeGraph):
    """The graph contribution extracted from one document."""

    document_id: str
    filename: str | None = None


class ExtractedEntity(BaseModel):
    """The small structured entity schema sent by the extraction model."""

    name: str
    type: str = "entity"
    aliases: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class ExtractedRelation(BaseModel):
    """The small structured relation schema sent by the extraction model."""

    source: str
    target: str
    label: str
    properties: dict[str, Any] = Field(default_factory=dict)


class ExtractedGraph(BaseModel):
    """Model output for one text chunk."""

    entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)


def _normalise_text(value: str) -> str:
    """Make entity/relation keys stable across documents."""
    value = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"[\s\u3000\-_/.,，。:：;；()（）\[\]【】]+", "", value)


def _entity_id(entity_type: str, name: str) -> str:
    """Return a deterministic, URL-safe id for an entity."""
    key = f"{_normalise_text(entity_type) or 'entity'}:{_normalise_text(name)}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return f"entity:{digest}"


def _edge_id(source: str, label: str, target: str) -> str:
    """Return a deterministic id for a relation."""
    digest = hashlib.sha1(
        f"{source}|{_normalise_text(label)}|{target}".encode("utf-8"),
    ).hexdigest()[:20]
    return f"edge:{digest}"


class KnowledgeGraphExtractor:
    """Extract graph facts from chunks with a structured-output model.

    ``model`` is optional so deployments without a graph model continue to
    behave exactly as before.  When it is ``None`` extraction returns an
    empty contribution and the vector pipeline remains usable.
    """

    def __init__(
        self,
        model: "ChatModelBase | None" = None,
        max_concurrency: int = 2,
        max_chars_per_chunk: int = 12000,
    ) -> None:
        self.model = model
        self._sem = asyncio.Semaphore(max(1, max_concurrency))
        self.max_chars_per_chunk = max(1000, max_chars_per_chunk)

    async def extract(
        self,
        chunks: "list[Chunk]",
        document_id: str,
        filename: str,
    ) -> DocumentGraph:
        """Extract and merge all chunk-level facts for one document."""
        if self.model is None or not chunks:
            return DocumentGraph(document_id=document_id, filename=filename)

        async def extract_one(chunk: "Chunk") -> tuple[int, ExtractedGraph]:
            async with self._sem:
                text_content = getattr(chunk.content, "text", "")
                if not text_content:
                    return chunk.chunk_index, ExtractedGraph()
                text = text_content[: self.max_chars_per_chunk]
                prompt = (
                    "请从下面的文档片段中抽取知识图谱信息。\n"
                    "只返回结构化结果，不要解释。\n"
                    "实体应使用片段中的规范名称；关系必须连接已抽取的实体。\n"
                    "无法确认的事实不要猜测。\n\n"
                    f"文档片段：\n{text}"
                )
                response = await self.model.generate_structured_output(
                    [
                        UserMsg(
                            name="knowledge-graph-extractor",
                            content=prompt,
                        ),
                    ],
                    ExtractedGraph,
                )
                return chunk.chunk_index, ExtractedGraph.model_validate(
                    response.content,
                )

        extracted = await asyncio.gather(
            *(extract_one(chunk) for chunk in chunks),
        )

        nodes: dict[str, GraphNode] = {}
        names_to_ids: dict[str, str] = {}
        chunk_metadata = {
            chunk.chunk_index: dict(chunk.metadata) for chunk in chunks
        }
        edge_candidates: list[tuple[int, ExtractedRelation]] = []
        for chunk_index, graph in extracted:
            ref = GraphSourceRef(
                document_id=document_id,
                chunk_index=chunk_index,
                filename=filename,
                metadata=chunk_metadata.get(chunk_index, {}),
            )
            for entity in graph.entities:
                if not entity.name.strip():
                    continue
                node_id = _entity_id(entity.type, entity.name)
                names_to_ids[_normalise_text(entity.name)] = node_id
                for alias in entity.aliases:
                    names_to_ids[_normalise_text(alias)] = node_id
                node = nodes.get(node_id)
                if node is None:
                    node = GraphNode(
                        id=node_id,
                        label=entity.name.strip(),
                        type=entity.type.strip() or "entity",
                        properties=dict(entity.properties),
                        aliases=list(entity.aliases),
                        source_refs=[ref],
                    )
                    nodes[node_id] = node
                else:
                    node.aliases = sorted(
                        set(node.aliases).union(entity.aliases),
                    )
                    node.properties.update(entity.properties)
                    if ref not in node.source_refs:
                        node.source_refs.append(ref)
            edge_candidates.extend(
                (chunk_index, relation) for relation in graph.relations
            )

        edges: dict[str, GraphEdge] = {}
        for chunk_index, relation in edge_candidates:
            source_id = names_to_ids.get(_normalise_text(relation.source))
            target_id = names_to_ids.get(_normalise_text(relation.target))
            if (
                source_id is None
                or target_id is None
                or not relation.label.strip()
            ):
                continue
            edge_id = _edge_id(source_id, relation.label, target_id)
            ref = GraphSourceRef(
                document_id=document_id,
                chunk_index=chunk_index,
                filename=filename,
                metadata=chunk_metadata.get(chunk_index, {}),
            )
            edge = edges.get(edge_id)
            if edge is None:
                edges[edge_id] = GraphEdge(
                    id=edge_id,
                    source=source_id,
                    target=target_id,
                    label=relation.label.strip(),
                    properties=dict(relation.properties),
                    source_refs=[ref],
                )
            else:
                edge.properties.update(relation.properties)
                if ref not in edge.source_refs:
                    edge.source_refs.append(ref)

        return DocumentGraph(
            document_id=document_id,
            filename=filename,
            nodes=list(nodes.values()),
            edges=list(edges.values()),
        )


__all__ = [
    "DocumentGraph",
    "ExtractedEntity",
    "ExtractedGraph",
    "ExtractedRelation",
    "GraphEdge",
    "GraphNode",
    "GraphSourceRef",
    "KnowledgeGraph",
    "KnowledgeGraphExtractor",
]

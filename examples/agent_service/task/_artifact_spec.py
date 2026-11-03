# -*- coding: utf-8 -*-
"""Task-owned semantic artifact specification and Markdown fallback."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ArtifactTable:
    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


@dataclass(frozen=True, slots=True)
class ArtifactSection:
    title: str
    paragraphs: tuple[str, ...] = ()
    bullets: tuple[str, ...] = ()
    table_indexes: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    title: str
    subtitle: str = ""
    summary: tuple[tuple[str, str], ...] = ()
    sections: tuple[ArtifactSection, ...] = ()
    tables: tuple[ArtifactTable, ...] = ()
    notes: tuple[str, ...] = ()


_ARTIFACT_SPEC_PROMPT = """请把下面的最终任务结果整理为文件产物结构，不要改写事实，不要补造数据。
只返回一个合法 JSON 对象，不要 Markdown 代码围栏，不要解释文字。结构必须是：
{
  "title": "文件标题",
  "subtitle": "时间范围、地点或数据来源等简短上下文",
  "summary": [{"label": "字段名", "value": "字段值"}],
  "sections": [{"title": "章节名", "paragraphs": ["短段落"], "bullets": ["短事项"], "table_indexes": [0]}],
  "tables": [{"title": "表名", "columns": ["列名"], "rows": [["值"]]}],
  "notes": ["待确认或数据局限"]
}
要求：
- tables 只放适合逐行、逐列比较的数据，不要把长段落拆成两列。
- 对天气、交通、价格、清单等重复记录，优先形成一张字段明确的表。
- 日期、数字、温度等值保留原值；没有数据就写“待确认”，不要猜测。
- summary 只放读者首先需要看到的结论或关键条件。
- sections 中的 table_indexes 从 0 开始，且必须引用实际存在的表。
- 不要添加“文件保存建议”“生成说明”等与用户业务无关的内容。

最终任务结果：
"""


def artifact_spec_prompt(output: str) -> str:
    return f"{_ARTIFACT_SPEC_PROMPT}\n{output[:30000]}"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return _text(value)


def _table_from_mapping(value: Any) -> ArtifactTable | None:
    if not isinstance(value, dict):
        return None
    columns = tuple(_text(item) for item in value.get("columns", []) if _text(item))
    raw_rows = value.get("rows", [])
    if not columns or not isinstance(raw_rows, list):
        return None
    rows: list[tuple[Any, ...]] = []
    for raw_row in raw_rows:
        if isinstance(raw_row, dict):
            row = tuple(_scalar(raw_row.get(column, "")) for column in columns)
        elif isinstance(raw_row, (list, tuple)):
            row = tuple(_scalar(item) for item in raw_row[: len(columns)])
        else:
            row = (_scalar(raw_row),)
        rows.append(row + ("",) * max(0, len(columns) - len(row)))
    return ArtifactTable(
        title=_text(value.get("title")) or "数据明细",
        columns=columns,
        rows=tuple(rows),
    )


def _extract_json(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def parse_agent_spec(raw: str, source: str) -> ArtifactSpec:
    """Validate an Agent-produced JSON spec, falling back without data loss."""

    payload = _extract_json(raw)
    if payload is None:
        return spec_from_markdown(source)
    tables = tuple(
        table
        for item in payload.get("tables", [])
        if (table := _table_from_mapping(item)) is not None
    )
    if not tables:
        return spec_from_markdown(source)
    sections: list[ArtifactSection] = []
    for item in payload.get("sections", []):
        if not isinstance(item, dict):
            continue
        indexes = tuple(
            int(index)
            for index in item.get("table_indexes", [])
            if isinstance(index, int) and 0 <= index < len(tables)
        )
        sections.append(
            ArtifactSection(
                title=_text(item.get("title")) or "内容",
                paragraphs=tuple(_text(value) for value in item.get("paragraphs", []) if _text(value)),
                bullets=tuple(_text(value) for value in item.get("bullets", []) if _text(value)),
                table_indexes=indexes,
            ),
        )
    summary = tuple(
        (_text(item.get("label")), _text(item.get("value")))
        for item in payload.get("summary", [])
        if isinstance(item, dict) and _text(item.get("label"))
    )
    if not sections:
        sections = [ArtifactSection(title="数据明细", table_indexes=tuple(range(len(tables))))]
    return ArtifactSpec(
        title=_text(payload.get("title")) or "任务结果报告",
        subtitle=_text(payload.get("subtitle")),
        summary=summary,
        sections=tuple(sections),
        tables=tables,
        notes=tuple(_text(value) for value in payload.get("notes", []) if _text(value)),
    )


def _split_row(line: str) -> tuple[str, ...]:
    content = line.strip().strip("|")
    return tuple(_clean_inline(item.strip()) for item in content.split("|"))


def _is_delimiter(line: str) -> bool:
    cells = _split_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", item.replace(" ", "")) for item in cells)


def _clean_inline(text: str) -> str:
    text = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    return text.replace("**", "").replace("__", "").replace("`", "").replace("*", "").strip()


def spec_from_markdown(markdown: str) -> ArtifactSpec:
    """Deterministic fallback for preview mode or a malformed Agent response."""

    lines = markdown.splitlines()
    title = "任务结果报告"
    subtitle = ""
    sections: list[dict[str, Any]] = []
    tables: list[ArtifactTable] = []
    current: dict[str, Any] | None = None
    index = 0
    while index < len(lines):
        raw = lines[index].strip()
        if not raw:
            index += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", raw)
        if heading:
            text = _clean_inline(heading.group(2))
            if heading.group(1) == "#" and title == "任务结果报告":
                title = text
                current = None
            else:
                current = {"title": text, "paragraphs": [], "bullets": [], "table_indexes": []}
                sections.append(current)
            index += 1
            continue
        if index + 1 < len(lines) and "|" in raw and "|" in lines[index + 1] and _is_delimiter(lines[index + 1]):
            rows = [_split_row(raw)]
            index += 2
            while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                rows.append(_split_row(lines[index]))
                index += 1
            if rows:
                table = ArtifactTable(
                    title=current["title"] if current else f"数据明细 {len(tables) + 1}",
                    columns=rows[0],
                    rows=tuple(tuple(row) for row in rows[1:]),
                )
                tables.append(table)
                if current is None:
                    current = {"title": table.title, "paragraphs": [], "bullets": [], "table_indexes": []}
                    sections.append(current)
                current["table_indexes"].append(len(tables) - 1)
            continue
        bullet = re.match(r"^[-*+]\s+(.+)$", raw)
        numbered = re.match(r"^\d+[.)]\s+(.+)$", raw)
        text = _clean_inline(raw[1:] if raw.startswith(">") else raw)
        if current is None:
            if bullet or numbered:
                current = {"title": "内容", "paragraphs": [], "bullets": [], "table_indexes": []}
                sections.append(current)
                current["bullets"].append(_clean_inline((bullet or numbered).group(1)))
            elif not re.fullmatch(r"[-*_]{3,}", raw):
                if not subtitle:
                    subtitle = text
                else:
                    current = {"title": "内容", "paragraphs": [text], "bullets": [], "table_indexes": []}
                    sections.append(current)
        elif bullet or numbered:
            current["bullets"].append(_clean_inline((bullet or numbered).group(1)))
        elif not re.fullmatch(r"[-*_]{3,}", raw):
            current["paragraphs"].append(text)
        index += 1
    if not sections and subtitle:
        sections.append({"title": "内容", "paragraphs": [subtitle], "bullets": [], "table_indexes": []})
    normalized_sections = tuple(
        ArtifactSection(
            title=item["title"],
            paragraphs=tuple(item["paragraphs"]),
            bullets=tuple(item["bullets"]),
            table_indexes=tuple(item["table_indexes"]),
        )
        for item in sections
    )
    return ArtifactSpec(title=title, subtitle=subtitle, sections=normalized_sections, tables=tuple(tables))

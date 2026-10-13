# -*- coding: utf-8 -*-
"""Small deterministic output checks for the S02 lightweight draft template."""

from __future__ import annotations

import re
from typing import Any


S02_LIGHTWEIGHT_TEMPLATE_VERSION = "5.0.0"
_MAX_MARKDOWN_BYTES = 96_000
_CHINESE_NUMERALS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
}
_CHAPTER_TOKENS = {
    1: ("项目概况",),
    2: ("资料清单", "可信度"),
    3: ("能源基线", "计量口径"),
    4: ("设备台账", "现状"),
    5: ("分析方法", "工作计划"),
    6: ("方案建议",),
    7: ("预算", "进度边界"),
    8: ("风险", "待核实事项", "交付清单"),
}
_CHAPTER_HEADING = re.compile(
    r"^\s*#{1,3}\s+第(?P<chinese>[一二三四五六七八])章\s*[：:、.．]?\s*(?P<title>.+?)\s*$"
    r"|^\s*#{1,3}\s+(?P<arabic>[1-8])\s*[、.．:：]\s*(?P<title_alt>.+?)\s*$",
)
_PROTOCOL_MARKER = re.compile(
    r"(?im)^\s*```\s*task-(?:claim-evidence|citations|evidence-claims)\s*$"
    r"|<!--\s*task-claim:",
)
_PENDING_MARKER = re.compile(
    r"待核实|待补充|待确认|尚未核实|尚待确认|无待核实事项",
)


def validate_s02_lightweight_markdown(output: str) -> dict[str, Any]:
    """Validate only the business-draft shape; never attest evidence semantics."""

    if not isinstance(output, str) or not output.strip():
        return _failed("TASK_S02_LIGHTWEIGHT_OUTPUT_EMPTY", "报告内容为空。")

    try:
        output_bytes = len(output.encode("utf-8"))
    except UnicodeEncodeError:
        return _failed(
            "TASK_S02_LIGHTWEIGHT_OUTPUT_ENCODING_INVALID",
            "报告包含无法编码的文本。",
        )
    if output_bytes > _MAX_MARKDOWN_BYTES:
        return _failed(
            "TASK_S02_LIGHTWEIGHT_OUTPUT_TOO_LARGE",
            "报告超过轻量草稿的大小上限。",
        )
    if _PROTOCOL_MARKER.search(output):
        return _failed(
            "TASK_S02_LIGHTWEIGHT_PROTOCOL_BLOCK_UNEXPECTED",
            "轻量草稿应为业务 Markdown，不能包含底层 Claim–Evidence 协议区块。",
        )

    lines = output.splitlines()
    chapters: list[tuple[int, int, str]] = []
    issues: list[str] = []
    for line_index, line in enumerate(lines):
        match = _CHAPTER_HEADING.match(line)
        if match is None:
            continue
        raw_number = match.group("chinese")
        number = (
            _CHINESE_NUMERALS.get(raw_number)
            if raw_number is not None
            else int(match.group("arabic"))
        )
        title = match.group("title") or match.group("title_alt") or ""
        expected_tokens = _CHAPTER_TOKENS[number]
        if not all(token in title for token in expected_tokens):
            issues.append(f"第{number}章标题未满足预期章节名称。")
        chapters.append((number, line_index, title))

    expected_numbers = list(range(1, 9))
    actual_numbers = [number for number, _, _ in chapters]
    if actual_numbers != expected_numbers:
        issues.append("报告必须按顺序包含且仅包含第一章至第八章。")

    if actual_numbers == expected_numbers:
        for index, (number, start, _) in enumerate(chapters):
            end = chapters[index + 1][1] if index + 1 < len(chapters) else len(lines)
            body_lines = [
                line.strip()
                for line in lines[start + 1 : end]
                if line.strip() and not re.fullmatch(r"[-*_]{3,}", line.strip())
            ]
            if not body_lines:
                issues.append(f"第{number}章缺少正文内容。")

    pending_marker_present = bool(_PENDING_MARKER.search(output))
    if not pending_marker_present:
        issues.append("报告必须明确标记待核实、待补充或待确认事项。")

    if issues:
        return {
            "passed": False,
            "error_code": "TASK_S02_LIGHTWEIGHT_OUTPUT_INVALID",
            "issues": issues,
            "chapter_count": len(chapters),
            "completed_chapters": [
                f"第{number}章" for number, _, _ in chapters
            ],
            "pending_marker_present": pending_marker_present,
            "output_bytes": output_bytes,
        }

    return {
        "passed": True,
        "error_code": None,
        "issues": [],
        "chapter_count": 8,
        "completed_chapters": [f"第{number}章" for number in expected_numbers],
        "pending_marker_present": True,
        "output_bytes": output_bytes,
    }


def _failed(code: str, message: str) -> dict[str, Any]:
    return {
        "passed": False,
        "error_code": code,
        "issues": [message],
        "chapter_count": 0,
        "completed_chapters": [],
        "pending_marker_present": False,
        "output_bytes": 0,
    }

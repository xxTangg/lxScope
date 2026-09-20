# -*- coding: utf-8 -*-
"""Task-owned artifact serialization for Workspace-backed output files."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ._artifact_spec import ArtifactSpec
from ._models import ArtifactConfig, ArtifactFormat
from ._office_renderers import render_docx, render_xlsx

_MAX_PREVIEW_CHARS = 12_000
_FORMAT_EXTENSIONS = {
    ArtifactFormat.MARKDOWN: ".md",
    ArtifactFormat.DOCX: ".docx",
    ArtifactFormat.XLSX: ".xlsx",
}
_MEDIA_TYPES = {
    ArtifactFormat.MARKDOWN: "text/markdown; charset=utf-8",
    ArtifactFormat.DOCX: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    ArtifactFormat.XLSX: (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
}


@dataclass(frozen=True, slots=True)
class GeneratedArtifact:
    """Serialized artifact ready for a Workspace backend write."""

    name: str
    format: ArtifactFormat
    media_type: str
    data: bytes
    preview_text: str


def generate_artifact(
    *,
    output: str,
    config: ArtifactConfig,
    default_stem: str,
    spec: ArtifactSpec | None = None,
) -> GeneratedArtifact:
    """Render the final Task output in the requested artifact format."""

    name = _safe_filename(config.filename, config.format, default_stem)
    if config.format is ArtifactFormat.MARKDOWN:
        data = output.encode("utf-8")
    elif config.format is ArtifactFormat.DOCX:
        data = render_docx(spec or output)
    elif config.format is ArtifactFormat.XLSX:
        data = render_xlsx(spec or output)
    else:  # pragma: no cover - guarded by the Pydantic enum
        raise ValueError(f"Unsupported artifact format: {config.format!r}")
    return GeneratedArtifact(
        name=name,
        format=config.format,
        media_type=_MEDIA_TYPES[config.format],
        data=data,
        preview_text=output[:_MAX_PREVIEW_CHARS],
    )


def _safe_filename(
    filename: str | None,
    format: ArtifactFormat,
    default_stem: str,
) -> str:
    """Keep the configured name a safe single Workspace path component."""

    extension = _FORMAT_EXTENSIONS[format]
    candidate = (filename or "").strip().replace("\\", "/").split("/")[-1]
    candidate = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff ]+", "_", candidate)
    candidate = candidate.strip(" .")
    for known_extension in _FORMAT_EXTENSIONS.values():
        if candidate.lower().endswith(known_extension):
            candidate = candidate[: -len(known_extension)].rstrip(" .")
            break
    candidate = candidate or default_stem
    return f"{candidate[: 120 - len(extension)]}{extension}"

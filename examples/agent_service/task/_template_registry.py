# -*- coding: utf-8 -*-
"""Load and validate the read-only built-in Task template catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ._template_models import TaskTemplateDefinition


TEMPLATE_FILES: tuple[tuple[str, str], ...] = (
    ("s01_project_init", "s01_project_init.v1.json"),
    ("s02_project_planning", "s02_project_planning.v1.json"),
    ("s03_expert_matching", "s03_expert_matching.v1.json"),
    ("s04_meeting_preparation", "s04_meeting_preparation.v1.json"),
    ("s05_meeting_minutes", "s05_meeting_minutes.v1.json"),
    ("s06_opinion_processing", "s06_opinion_processing.v1.json"),
    ("s07_report_generation", "s07_report_generation.v1.json"),
    ("s08_archive_transfer", "s08_archive_transfer.v1.json"),
    ("s09_expense_settlement", "s09_expense_settlement.v1.json"),
)


class TemplateRegistryError(ValueError):
    """Safe, locatable failure while reading a built-in template file."""

    def __init__(
        self,
        code: str,
        *,
        file_name: str | None = None,
        template_id: str | None = None,
        issues: tuple[str, ...] = (),
    ) -> None:
        self.code = code
        self.file_name = file_name
        self.template_id = template_id
        self.issues = issues
        location = file_name or "template catalog"
        super().__init__(f"{code} in {location}")

    def public_detail(self) -> dict[str, Any]:
        """Return a diagnostic response without exposing filesystem paths."""

        detail: dict[str, Any] = {
            "code": "task_template_registry_error",
            "message": "内置 Task 模板配置不可用。",
            "reason": self.code,
        }
        if self.file_name is not None:
            detail["file"] = self.file_name
        if self.template_id is not None:
            detail["template_id"] = self.template_id
        if self.issues:
            detail["issues"] = list(self.issues)
        return detail


class _DuplicateJSONKey(ValueError):
    """Raised when a JSON object repeats a key."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey(key)
        result[key] = value
    return result


class TemplateRegistry:
    """Read the nine packaged templates independently of process cwd."""

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = (
            Path(root)
            if root is not None
            else Path(__file__).resolve().parent / "templates"
        )

    def list_templates(self) -> list[TaskTemplateDefinition]:
        """Return the catalog in stable S01 through S09 order."""

        if not self._root.is_dir():
            raise TemplateRegistryError("template_directory_missing")

        expected_names = {file_name for _, file_name in TEMPLATE_FILES}
        actual_names = {
            path.name for path in self._root.glob("*.json") if path.is_file()
        }
        unexpected = sorted(actual_names - expected_names)
        if unexpected:
            raise TemplateRegistryError(
                "unexpected_template_file",
                file_name=unexpected[0],
            )

        templates: list[TaskTemplateDefinition] = []
        for expected_id, file_name in TEMPLATE_FILES:
            path = self._root / file_name
            if not path.is_file():
                raise TemplateRegistryError(
                    "template_file_missing",
                    file_name=file_name,
                    template_id=expected_id,
                )
            templates.append(self._load(path, expected_id))
        return templates

    def get_template(
        self,
        template_id: str,
    ) -> TaskTemplateDefinition | None:
        """Return one template by its stable ID, or ``None`` if unknown."""

        for template in self.list_templates():
            if template.template_id == template_id:
                return template
        return None

    def _load(
        self,
        path: Path,
        expected_id: str,
    ) -> TaskTemplateDefinition:
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise TemplateRegistryError(
                "template_file_unreadable",
                file_name=path.name,
                template_id=expected_id,
            ) from error

        try:
            payload = json.loads(raw, object_pairs_hook=_unique_object)
        except _DuplicateJSONKey as error:
            raise TemplateRegistryError(
                "duplicate_json_key",
                file_name=path.name,
                template_id=expected_id,
                issues=("JSON object keys must be unique.",),
            ) from error
        except json.JSONDecodeError as error:
            raise TemplateRegistryError(
                "invalid_json",
                file_name=path.name,
                template_id=expected_id,
                issues=(f"Invalid JSON at line {error.lineno}, column {error.colno}.",),
            ) from error

        try:
            template = TaskTemplateDefinition.model_validate(payload)
        except ValidationError as error:
            issues = tuple(
                f"{'.'.join(str(part) for part in issue['loc'])} "
                f"[{issue['type']}]"
                for issue in error.errors(include_url=False)
            )
            raise TemplateRegistryError(
                "invalid_template_schema",
                file_name=path.name,
                template_id=expected_id,
                issues=issues,
            ) from error

        if template.template_id != expected_id:
            raise TemplateRegistryError(
                "template_id_filename_mismatch",
                file_name=path.name,
                template_id=template.template_id,
                issues=(f"Expected template_id {expected_id}.",),
            )
        return template

# -*- coding: utf-8 -*-
"""Load and validate the read-only built-in Task template catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ._template_models import TaskTemplateDefinition


TEMPLATE_VERSION_FILES: tuple[tuple[str, str, str], ...] = (
    ("s01_project_init", "1.0.0", "s01_project_init.v1.json"),
    ("s01_project_init", "2.0.0", "s01_project_init.v2.json"),
    ("s01_project_init", "3.0.0", "s01_project_init.v3.json"),
    ("s02_project_planning", "1.0.0", "s02_project_planning.v1.json"),
    ("s02_project_planning", "2.0.0", "s02_project_planning.v2.json"),
    ("s02_project_planning", "3.0.0", "s02_project_planning.v3.json"),
    ("s02_project_planning", "3.1.0", "s02_project_planning.v3_1.json"),
    ("s02_project_planning", "3.2.0", "s02_project_planning.v3_2.json"),
    ("s02_project_planning", "4.0.0", "s02_project_planning.v4.json"),
    ("s02_project_planning", "4.1.0", "s02_project_planning.v4_1.json"),
    ("s02_project_planning", "5.0.0", "s02_project_planning.v5.json"),
    ("s02_project_planning", "5.1.0", "s02_project_planning.v5_1.json"),
    ("s03_expert_matching", "1.0.0", "s03_expert_matching.v1.json"),
    ("s03_expert_matching", "2.0.0", "s03_expert_matching.v2.json"),
    ("s03_expert_matching", "3.0.0", "s03_expert_matching.v3.json"),
    ("s04_meeting_preparation", "1.0.0", "s04_meeting_preparation.v1.json"),
    ("s04_meeting_preparation", "2.0.0", "s04_meeting_preparation.v2.json"),
    ("s04_meeting_preparation", "3.0.0", "s04_meeting_preparation.v3.json"),
    ("s05_meeting_minutes", "1.0.0", "s05_meeting_minutes.v1.json"),
    ("s05_meeting_minutes", "2.0.0", "s05_meeting_minutes.v2.json"),
    ("s05_meeting_minutes", "3.0.0", "s05_meeting_minutes.v3.json"),
    ("s06_opinion_processing", "1.0.0", "s06_opinion_processing.v1.json"),
    ("s06_opinion_processing", "2.0.0", "s06_opinion_processing.v2.json"),
    ("s06_opinion_processing", "3.0.0", "s06_opinion_processing.v3.json"),
    ("s07_report_generation", "1.0.0", "s07_report_generation.v1.json"),
    ("s07_report_generation", "2.0.0", "s07_report_generation.v2.json"),
    ("s07_report_generation", "3.0.0", "s07_report_generation.v3.json"),
    ("s07_report_generation", "4.0.0", "s07_report_generation.v4.json"),
    ("s07_report_generation", "5.0.0", "s07_report_generation.v5.json"),
    ("s08_archive_transfer", "1.0.0", "s08_archive_transfer.v1.json"),
    ("s08_archive_transfer", "2.0.0", "s08_archive_transfer.v2.json"),
    ("s08_archive_transfer", "3.0.0", "s08_archive_transfer.v3.json"),
    ("s09_expense_settlement", "1.0.0", "s09_expense_settlement.v1.json"),
    ("s09_expense_settlement", "2.0.0", "s09_expense_settlement.v2.json"),
    ("s09_expense_settlement", "3.0.0", "s09_expense_settlement.v3.json"),
)

# Keep the public gallery pinned to one explicitly selected version per
# scenario.
# Historical files may be added to TEMPLATE_VERSION_FILES without changing
# this tuple. Gallery/current lookup exposes only these versions; callers that
# explicitly request an exact published version can resolve its immutable
# definition through get_template_version().
CURRENT_TEMPLATE_VERSIONS: tuple[tuple[str, str], ...] = (
    ("s01_project_init", "3.0.0"),
    ("s02_project_planning", "5.1.0"),
    ("s03_expert_matching", "3.0.0"),
    ("s04_meeting_preparation", "3.0.0"),
    ("s05_meeting_minutes", "3.0.0"),
    ("s06_opinion_processing", "3.0.0"),
    ("s07_report_generation", "5.0.0"),
    ("s08_archive_transfer", "3.0.0"),
    ("s09_expense_settlement", "3.0.0"),
)

# Compatibility view for code and tests that used the original ID/file catalog.
TEMPLATE_FILES: tuple[tuple[str, str], ...] = tuple(
    (template_id, file_name)
    for template_id, template_version, file_name in TEMPLATE_VERSION_FILES
    if (template_id, template_version) in CURRENT_TEMPLATE_VERSIONS
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
    """Read the explicitly versioned template catalog independently of cwd."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        version_files: tuple[tuple[str, str, str], ...] | None = None,
        current_versions: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        self._root = (
            Path(root)
            if root is not None
            else Path(__file__).resolve().parent / "templates"
        )
        self._version_files = (
            TEMPLATE_VERSION_FILES if version_files is None else version_files
        )
        self._current_versions = (
            CURRENT_TEMPLATE_VERSIONS
            if current_versions is None
            else current_versions
        )

    def list_templates(self) -> list[TaskTemplateDefinition]:
        """Return one current template per scenario in catalog order."""

        files_by_version = self._validate_catalog_files()
        templates: list[TaskTemplateDefinition] = []
        for template_id, template_version in self._current_versions:
            file_name = files_by_version[(template_id, template_version)]
            path = self._root / file_name
            if not path.is_file():
                raise TemplateRegistryError(
                    "template_file_missing",
                    file_name=file_name,
                    template_id=template_id,
                )
            templates.append(
                self._load(
                    path,
                    template_id,
                    template_version,
                )
            )
        return templates

    def get_current_template(
        self,
        template_id: str,
    ) -> TaskTemplateDefinition | None:
        """Return the selected current version for one scenario, if present."""

        for template in self.list_templates():
            if template.template_id == template_id:
                return template
        return None

    def get_template(
        self,
        template_id: str,
    ) -> TaskTemplateDefinition | None:
        """Compatibility alias for :meth:`get_current_template`."""

        return self.get_current_template(template_id)

    def get_template_version(
        self,
        template_id: str,
        template_version: str,
    ) -> TaskTemplateDefinition | None:
        """Return one declared immutable definition by exact ID and version."""

        files_by_version = self._validate_catalog_files()
        file_name = files_by_version.get((template_id, template_version))
        if file_name is None:
            return None
        path = self._root / file_name
        if not path.is_file():
            raise TemplateRegistryError(
                "template_file_missing",
                file_name=file_name,
                template_id=template_id,
            )
        return self._load(
            path,
            template_id,
            template_version,
        )

    def _validate_catalog_files(self) -> dict[tuple[str, str], str]:
        """Validate the manifest and reject every undeclared JSON file."""

        if not self._root.is_dir():
            raise TemplateRegistryError("template_directory_missing")

        files_by_version: dict[tuple[str, str], str] = {}
        names_by_file: dict[str, tuple[str, str]] = {}
        for template_id, template_version, file_name in self._version_files:
            key = (template_id, template_version)
            if (
                key in files_by_version
                or file_name in names_by_file
                or Path(file_name).name != file_name
            ):
                raise TemplateRegistryError(
                    "invalid_template_manifest",
                    file_name=file_name,
                    template_id=template_id,
                )
            files_by_version[key] = file_name
            names_by_file[file_name] = key

        current_ids: set[str] = set()
        for template_id, template_version in self._current_versions:
            if (
                template_id in current_ids
                or (template_id, template_version) not in files_by_version
            ):
                raise TemplateRegistryError(
                    "invalid_template_manifest",
                    template_id=template_id,
                )
            current_ids.add(template_id)
        if not current_ids or current_ids != {
            template_id for template_id, _, _ in self._version_files
        }:
            raise TemplateRegistryError("invalid_template_manifest")

        actual_names = {
            path.name for path in self._root.glob("*.json") if path.is_file()
        }
        unexpected = sorted(actual_names - set(names_by_file))
        if unexpected:
            raise TemplateRegistryError(
                "unexpected_template_file",
                file_name=unexpected[0],
            )
        return files_by_version

    def _load(
        self,
        path: Path,
        expected_id: str,
        expected_version: str,
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
                issues=(
                    f"Invalid JSON at line {error.lineno}, "
                    f"column {error.colno}.",
                ),
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
        if template.template_version != expected_version:
            raise TemplateRegistryError(
                "template_version_manifest_mismatch",
                file_name=path.name,
                template_id=template.template_id,
                issues=(f"Expected template_version {expected_version}.",),
            )
        return template

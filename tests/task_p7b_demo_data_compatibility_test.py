"""Schema and runtime compatibility checks for the P7-B demo package.

These tests exercise only Task-local deterministic adapters with explicitly
synthetic inputs. They are not live RAG, browser, model, Workspace, or business
system acceptance tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

from examples.agent_service.task._scenario_runtime import (
    execute_scenario_capability,
)
from examples.agent_service.task._template_compiler import TaskTemplateCompiler
from examples.agent_service.task._template_registry import TemplateRegistry


_DEMO_ROOT = (
    Path(__file__).resolve().parents[1]
    / "examples/agent_service/task/docs/p7b-demo-data"
)
_SCENARIOS = {
    "S01": ("s01_project_init", "s01-project-init.v2.json", "2.0.0"),
    "S02": (
        "s02_project_planning",
        "s02-project-planning.v2.json",
        "2.0.0",
    ),
    "S03": (
        "s03_expert_matching",
        "s03-expert-matching.v3.json",
        "3.0.0",
    ),
    "S04": (
        "s04_meeting_preparation",
        "s04-meeting-preparation.v3.json",
        "3.0.0",
    ),
    "S05": (
        "s05_meeting_minutes",
        "s05-meeting-minutes.v3.json",
        "3.0.0",
    ),
    "S06": (
        "s06_opinion_processing",
        "s06-opinion-processing.v3.json",
        "3.0.0",
    ),
    "S07": (
        "s07_report_generation",
        "s07-report-generation.v3.json",
        "4.0.0",
    ),
    "S08": (
        "s08_archive_transfer",
        "s08-archive-transfer.v3.json",
        "3.0.0",
    ),
    "S09": (
        "s09_expense_settlement",
        "s09-expense-settlement.v3.json",
        "3.0.0",
    ),
}


def _load_inputs(file_name: str) -> dict:
    payload = json.loads(
        (_DEMO_ROOT / "inputs" / file_name).read_text(encoding="utf-8")
    )
    return payload["inputs"]


def _run(capability: str, values: dict[str, str], prior=None):
    return execute_scenario_capability(
        capability,
        input_values=values,
        prior_results=prior or {},
        run_id="synthetic-p7b-demo-compatibility",
    )


class DemoTemplateCompatibilityTest(TestCase):
    def test_all_nine_demo_inputs_match_their_declared_template_versions(self):
        registry = TemplateRegistry()
        compiler = TaskTemplateCompiler()
        for scenario, (template_id, file_name, version) in _SCENARIOS.items():
            with self.subTest(scenario=scenario):
                # Demo input files are intentionally versioned with their
                # original template contract. They remain useful for
                # compatibility checks after a newer gallery version ships.
                template = registry.get_template_version(template_id, version)
                self.assertIsNotNone(template)
                self.assertEqual(template.template_version, version)
                inputs = _load_inputs(file_name)
                self.assertEqual(compiler.validate_inputs(template, inputs), inputs)
                self.assertLessEqual(len(template.steps), 8)
                self.assertTrue(
                    any(step.artifact is not None for step in template.steps)
                )

        s07 = _load_inputs(_SCENARIOS["S07"][1])
        self.assertEqual(
            s07["source_material_ids"],
            ["__REPLACE_WITH_SERVER_ISSUED_AUTHORIZED_DOCUMENT_ID__"],
        )

    def test_nested_json_values_are_decoded_once_and_s04_rate_sample_runs(self):
        s04 = _load_inputs(_SCENARIOS["S04"][1])
        fee_rates = json.loads(s04["fee_rates_json"])
        self.assertEqual(fee_rates["rule_id"], "DEMO-FEE-RULE-2025-01")
        costs = _run(
            "meeting.cost_calculate",
            {
                **s04,
                "participant_count": str(s04["participant_count"]),
            },
        )
        self.assertEqual(costs.status, "completed", costs.reason)
        self.assertEqual(costs.result["total"], "3560.00")
        self.assertFalse(costs.result["official_fee_policy_verified"])

        # Copying the outer JSON string including its quotes/backslash escapes
        # into the existing textarea is an invalid JSON object and fails closed.
        over_escaped = json.dumps(s04["fee_rates_json"], ensure_ascii=False)
        rejected = _run(
            "meeting.cost_calculate",
            {**s04, "fee_rates_json": over_escaped},
        )
        self.assertEqual(rejected.status, "failed")

    def test_s05_s06_s08_s09_samples_follow_the_runtime_textarea_protocol(self):
        s05 = _load_inputs(_SCENARIOS["S05"][1])
        transcript = _run(
            "meeting.transcript_import",
            {
                **s05,
                "agenda": json.dumps(s05["agenda"], ensure_ascii=False),
            },
        )
        self.assertEqual(transcript.status, "completed", transcript.reason)
        self.assertFalse(transcript.result["transcription_performed"])
        self.assertEqual(transcript.result["agenda"], s05["agenda"])

        s06 = _load_inputs(_SCENARIOS["S06"][1])
        imported_opinions = _run("opinion.text_import", s06)
        classified = _run(
            "opinion.classify",
            s06,
            {"opinion.text_import": imported_opinions.result},
        )
        self.assertEqual(imported_opinions.status, "completed")
        self.assertEqual(classified.status, "completed", classified.reason)
        self.assertEqual(classified.result["original_count"], 4)
        self.assertEqual(classified.result["duplicate_count"], 1)
        self.assertFalse(classified.result["ocr_performed"])

        s08 = _load_inputs(_SCENARIOS["S08"][1])
        manifest = _run("archive.document_metadata", s08)
        completeness = _run(
            "archive.completeness_check",
            s08,
            {"archive.document_metadata": manifest.result},
        )
        self.assertEqual(manifest.status, "completed", manifest.reason)
        self.assertEqual(completeness.status, "completed", completeness.reason)
        self.assertEqual(completeness.result["incomplete_count"], 0)
        self.assertFalse(completeness.result["archive_system_write_performed"])

        s09 = _load_inputs(_SCENARIOS["S09"][1])
        invoices = _run("expense.invoice_import", s09)
        deduped = _run(
            "expense.invoice_verify_dedupe",
            s09,
            {"expense.invoice_import": invoices.result},
        )
        checked = _run(
            "expense.rule_check",
            s09,
            {"expense.invoice_verify_dedupe": deduped.result},
        )
        self.assertEqual(invoices.status, "completed", invoices.reason)
        self.assertEqual(deduped.status, "completed", deduped.reason)
        self.assertEqual(checked.status, "completed", checked.reason)
        self.assertEqual(checked.result["total"], "2120.00")
        self.assertEqual(checked.result["violations"], [])
        self.assertFalse(checked.result["invoice_authenticity_verified"])
        self.assertFalse(checked.result["erp_submitted"])

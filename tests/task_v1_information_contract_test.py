# -*- coding: utf-8 -*-
"""Task-V1 contract authority and AI-verification boundary regressions."""

import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from examples.agent_service.task._agentscope_executor import AgentScopeTaskExecutor
from examples.agent_service.task._clarification import EffectiveRequirements
from examples.agent_service.task._execution_gates import (
    enforce_json_output_contract,
)
from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._information_contract import (
    arbitrate_requirements,
    build_information_contract,
    enforce_verifier_boundary,
    information_contract_prompt_view,
)
from examples.agent_service.task._models import (
    AgentStepConfig,
    ExecutionContext,
    InformationCondition,
    InputStepConfig,
    NodeOutputContract,
    StepType,
    TaskGraph,
    TaskGraphEdge,
    TaskInformationRequirement,
    TaskInputQuestion,
    TaskInputReply,
    TaskNode,
    StepVerdict,
    StepVerificationIssue,
)
from examples.agent_service.task._service import _node_snapshot_fingerprint
from examples.agent_service.task._template_compiler import TaskTemplateCompiler
from examples.agent_service.task._template_registry import TemplateRegistry


def _requirements(*fields):
    return EffectiveRequirements.model_validate(
        {"effective_goal": "完成当前任务", "fields": list(fields)}
    )


def _field(key, *, value="", blocking=False, status=None):
    return {
        "key": key,
        "label": key,
        "value": value,
        "status": status or ("provided" if value else "unknown"),
        "blocking": blocking,
        "blocking_reason": "模型推断该字段缺失" if blocking else "",
    }


def _context(**updates):
    return ExecutionContext(
        user_id="v1-owner",
        task_id="v1-task",
        run_id="v1-run",
        task_revision=1,
        agent_id="v1-agent",
        session_id="v1-session",
        initial_input="合成隔离测试输入",
        **updates,
    )


def _frozen_contract(
    nodes,
    node,
    *,
    graph=None,
    template_id=None,
    template_version=None,
    fingerprint=None,
):
    return build_information_contract(
        nodes=nodes,
        graph=graph,
        node_id=node.id,
        source_template_id=template_id,
        source_template_version=template_version,
        snapshot_fingerprint=fingerprint or _node_snapshot_fingerprint(nodes),
        run_id="v1-run",
    )


class TaskInformationContractTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = AsyncMock()
        self.storage.get_session.return_value = SimpleNamespace(
            config=SimpleNamespace(chat_model_config=object())
        )
        self.storage.get_agent.return_value = SimpleNamespace(
            data=SimpleNamespace(system_prompt="", name="V1 test agent")
        )
        self.model = AsyncMock()
        self.executor = AgentScopeTaskExecutor(
            storage=self.storage,
            resource_access_service=AsyncMock(),
        )
        self.model_patch = patch(
            "agentscope.app._service._model.get_model",
            return_value=self.model,
        )
        self.model_patch.start()
        self.addCleanup(self.model_patch.stop)

    async def test_s01_original_missing_rule_fields_are_pending_not_blockers(self):
        registry = TemplateRegistry()
        template = registry.get_template_version(
            "s01_project_init",
            "3.0.0",
        )
        self.assertIsNotNone(template)
        compiled = TaskTemplateCompiler().compile_nodes(
            template,
            [],
            require_tool_bindings=False,
        )
        node_id = compiled.step_bindings["draft_initiation_recommendation"]
        node = next(item for item in compiled.nodes if item.id == node_id)
        answers = [
            TaskInputReply(
                request_id="s01-input",
                answers={
                    "project_name": "演示项目",
                    "project_brief": "用于 Task-V1 的合成项目背景",
                },
            ),
        ]
        contract = _frozen_contract(
            compiled.nodes,
            node,
            template_id=template.template_id,
            template_version=template.template_version,
        )
        context = _context(
            source_template_id=template.template_id,
            source_template_version=template.template_version,
            user_answers=answers,
            information_contract=contract,
            history=[
                {
                    "node_id": "rule-check",
                    "structured_result": {
                        "status": "candidates_for_review",
                        "evidence": [{"source_id": "synthetic-source"}],
                        "automated_compliance_conclusion": False,
                    },
                },
            ],
        )
        self.model.generate_structured_output.side_effect = [
            SimpleNamespace(
                content={
                    "effective_goal": "形成待人工复核的立项建议草稿",
                    "fields": [
                        _field("formal_rule_source", blocking=True),
                        _field(
                            "rule_applicability_confirmation",
                            blocking=True,
                        ),
                    ],
                },
                usage=None,
            ),
            SimpleNamespace(
                content={
                    "status": "succeeded",
                    "output": (
                        "立项建议草稿：建议进入人工复核。\\n"
                        "正式规则来源：待核实。\\n"
                        "规则适用性确认方：待指定。\\n"
                        "未形成正式规则适用、合规或审批结论。"
                    ),
                },
                usage=None,
            ),
        ]

        result = await self.executor.execute_node(
            node=node,
            previous_output="候选规则结果",
            context=context,
        )

        self.assertEqual(result.status, "completed")
        self.assertNotIn(
            "已停止重复追问，但当前步骤仍缺少必要信息：正式规则/制度来源、"
            "规则适用性确认方。无法在不虚构的前提下继续执行。",
            result.text,
        )
        self.assertEqual(self.model.generate_structured_output.call_count, 2)
        effective = EffectiveRequirements.model_validate(
            result.metadata["clarification"]["requirements"]
        )
        self.assertEqual(effective.blockers, [])
        by_key = {field.key: field for field in effective.fields}
        for key in (
            "formal_rule_source",
            "rule_applicability_confirmation",
        ):
            self.assertEqual(by_key[key].status, "unknown")
            self.assertEqual(by_key[key].classification, "pending_verification")
            self.assertFalse(by_key[key].blocking)
            self.assertEqual(by_key[key].value, "")
        self.assertEqual(
            by_key["project_name"].value,
            "演示项目",
        )
        self.assertEqual(
            by_key["project_brief"].value,
            "用于 Task-V1 的合成项目背景",
        )
        generation_payload = json.loads(
            self.model.generate_structured_output.call_args_list[0]
            .kwargs["messages"][-1]
            .get_text_content()
        )
        self.assertEqual(
            generation_payload["frozen_information_contract"],
            information_contract_prompt_view(contract),
        )

    async def test_model_cannot_create_an_authoritative_blocker(self):
        requirements = _requirements(
            _field("undeclared_approval_owner", blocking=True)
        )
        result = arbitrate_requirements(
            requirements,
            information_contract={},
            user_answers=[],
            initial_input="",
            named_inputs={},
        )
        self.assertEqual(result.blockers, [])
        self.assertEqual(result.fields[0].classification, "pending_verification")
        self.assertFalse(result.fields[0].blocking)
        self.assertEqual(result.fields[0].status, "unknown")

    async def test_agent_failure_without_questions_remains_failed(self):
        node = TaskNode(
            id="draft",
            name="整理草稿",
            config=AgentStepConfig(prompt="根据已有信息整理待复核草稿"),
        )
        self.model.generate_structured_output.side_effect = [
            SimpleNamespace(
                content={"effective_goal": "整理草稿", "fields": []},
                usage=None,
            ),
            SimpleNamespace(
                content={
                    "status": "failed",
                    "reason": "缺少未声明的正式责任人，无法继续。",
                },
                usage=None,
            ),
        ]
        result = await self.executor.execute_node(
            node=node,
            previous_output="存在可用的业务候选结果",
            context=_context(),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.model.generate_structured_output.call_count, 2)
        self.assertEqual(
            result.metadata["agent_outcome_protocol"]["model_status"],
            "failed",
        )
        self.assertIn("未声明的正式责任人", result.reason)

    async def test_frozen_required_input_remains_a_blocker(self):
        input_node = TaskNode(
            id="input",
            name="收集项目编号",
            type=StepType.INPUT,
            config=InputStepConfig(
                questions=[
                    TaskInputQuestion(
                        key="project_code",
                        label="项目编号",
                        required=True,
                    ),
                ],
            ),
            order=0,
        )
        agent_node = TaskNode(
            id="draft",
            name="编制草稿",
            config=AgentStepConfig(prompt="编制草稿"),
            order=1,
        )
        contract = _frozen_contract([input_node, agent_node], agent_node)
        result = arbitrate_requirements(
            _requirements(_field("project_code")),
            information_contract=contract,
            user_answers=[],
            initial_input="",
            named_inputs={},
        )
        self.assertEqual([field.key for field in result.blockers], ["project_code"])
        self.assertEqual(
            result.fields[0].source_path,
            "run.nodes[0].config.questions.project_code.required",
        )

    async def test_only_successfully_validated_prior_values_satisfy_contract(self):
        node = TaskNode(
            id="draft",
            name="生成项目摘要",
            config=AgentStepConfig(prompt="使用已校验的项目编号"),
            information_requirements=[
                TaskInformationRequirement(
                    key="project_code",
                    label="项目编号",
                    classification="required",
                    reason="摘要需要项目编号。",
                ),
            ],
        )
        contract = _frozen_contract([node], node)
        model_value = _requirements(_field("project_code"))
        unverified = arbitrate_requirements(
            model_value,
            information_contract=contract,
            user_answers=[],
            initial_input="",
            named_inputs={},
            prior_results=[
                {
                    "output": "项目编号 P-1001",
                    "metadata": {
                        "task_ai_verification": {
                            "passed": False,
                        },
                    },
                },
            ],
        )
        self.assertTrue(unverified.fields[0].blocking)

        verified = arbitrate_requirements(
            model_value,
            information_contract=contract,
            user_answers=[],
            initial_input="",
            named_inputs={},
            prior_results=[
                {
                    "ai_verified_result": {"project_code": "P-1001"},
                },
            ],
        )
        self.assertFalse(verified.fields[0].blocking)
        self.assertEqual(verified.fields[0].value, "P-1001")
        self.assertEqual(verified.fields[0].status, "provided")

    async def test_conditional_requirement_only_blocks_when_condition_is_true(self):
        conditional = TaskInformationRequirement(
            key="supplier_invoice_id",
            label="供应商发票编号",
            classification="conditional_required",
            reason="供应商报销需要发票编号。",
            condition=InformationCondition(
                field="expense_type",
                operator="equals",
                value="supplier",
            ),
        )
        node = TaskNode(
            id="draft",
            name="费用草稿",
            config=AgentStepConfig(prompt="整理费用草稿"),
            information_requirements=[conditional],
        )
        contract = _frozen_contract([node], node)
        for expense_type, expected_blocked, expected_condition in (
            ("supplier", True, "active"),
            ("employee", False, "inactive"),
            (None, False, "unresolved"),
        ):
            answers = (
                [
                    TaskInputReply(
                        request_id="expense-type",
                        answers={"expense_type": expense_type},
                    ),
                ]
                if expense_type is not None
                else []
            )
            result = arbitrate_requirements(
                _requirements(_field("supplier_invoice_id", blocking=False)),
                information_contract=contract,
                user_answers=answers,
                initial_input="",
                named_inputs={},
            )
            field = result.fields[0]
            self.assertEqual(field.blocking, expected_blocked)
            self.assertEqual(field.condition_state, expected_condition)
            self.assertEqual(
                field.classification,
                "conditional_required",
            )

    async def test_human_confirmation_is_never_filled_from_model_output(self):
        node = TaskNode(
            id="draft",
            name="生成待审草稿",
            config=AgentStepConfig(prompt="仅生成待审草稿"),
            information_requirements=[
                TaskInformationRequirement(
                    key="reviewer_confirmation",
                    label="复核人确认",
                    classification="human_confirmation",
                    reason="需由人工明确确认复核责任人。",
                ),
            ],
        )
        contract = _frozen_contract([node], node)
        model_claim = _requirements(
            _field(
                "reviewer_confirmation",
                value="张某已经确认",
                status="provided",
            )
        )
        missing = arbitrate_requirements(
            model_claim,
            information_contract=contract,
            user_answers=[],
            initial_input="",
            named_inputs={},
        ).fields[0]
        self.assertEqual(missing.value, "")
        self.assertEqual(missing.status, "unknown")
        self.assertTrue(missing.blocking)
        self.assertEqual(missing.classification, "human_confirmation")

        answer = "用户答复：由张某负责复核"
        supplied = arbitrate_requirements(
            model_claim,
            information_contract=contract,
            user_answers=[
                TaskInputReply(
                    request_id="reviewer",
                    answers={"reviewer_confirmation": answer},
                ),
            ],
            initial_input="",
            named_inputs={},
        ).fields[0]
        self.assertEqual(supplied.value, answer)
        self.assertEqual(supplied.status, "provided")
        self.assertFalse(supplied.blocking)
        self.assertEqual(supplied.classification, "human_confirmation")

    async def test_verifier_rejection_requires_a_frozen_contract_quote(self):
        node = TaskNode(
            id="draft",
            name="草稿",
            config=AgentStepConfig(prompt="缺少信息时将事项标为待核实。"),
            acceptance_criteria="不得输出未经核验的正式结论。",
        )
        contract = _frozen_contract([node], node)

        unsubstantiated = enforce_verifier_boundary(
            StepVerdict(
                passed=False,
                message="缺少正式责任人，不能继续。",
            ),
            node=node,
            information_contract=contract,
        )
        self.assertTrue(unsubstantiated.passed)
        self.assertIn("非阻断待核实", unsubstantiated.message)
        self.assertTrue(unsubstantiated.issues)
        self.assertEqual(unsubstantiated.issues[0].category, "suggestion")

        supported = enforce_verifier_boundary(
            StepVerdict(
                passed=False,
                message="输出包含未核验的正式结论。",
                issues=[
                    StepVerificationIssue(
                        category="contract_violation",
                        message="不符合节点验收条件。",
                        source_path="node.acceptance_criteria",
                        contract_quote="不得输出未经核验的正式结论。",
                    ),
                ],
            ),
            node=node,
            information_contract=contract,
        )
        self.assertFalse(supported.passed)

        inconsistent_pass = enforce_verifier_boundary(
            StepVerdict(
                passed=True,
                message="通过",
                issues=[
                    StepVerificationIssue(
                        category="contract_violation",
                        message="含未经核验的正式结论。",
                        source_path="node.acceptance_criteria",
                        contract_quote="不得输出未经核验的正式结论。",
                    ),
                ],
            ),
            node=node,
            information_contract=contract,
        )
        self.assertFalse(inconsistent_pass.passed)

    async def test_generation_and_verifier_receive_same_frozen_contract(self):
        node = TaskNode(
            id="draft",
            name="编制建议",
            config=AgentStepConfig(prompt="形成有边界的建议稿"),
            acceptance_criteria="未核实事项须注明待核实。",
        )
        contract = _frozen_contract(
            [node],
            node,
            template_id="custom_flow",
            template_version="2.4.0",
            fingerprint="f" * 64,
        )
        self.model.generate_structured_output.side_effect = [
            SimpleNamespace(
                content={"effective_goal": "形成草稿", "fields": []},
                usage=None,
            ),
            SimpleNamespace(content={"passed": True}, usage=None),
        ]
        context = _context(
            information_contract=contract,
            clarification_state={
                "requirements": {
                    "effective_goal": "形成草稿",
                    "fields": [],
                },
            },
        )
        await self.executor._resolve_requirements(
            self.model,
            node,
            "",
            context,
        )
        await self.executor.verify_node(
            node=node,
            output="建议草稿；未核实事项：待核实。",
            context=context,
        )
        generation = json.loads(
            self.model.generate_structured_output.call_args_list[0]
            .kwargs["messages"][-1]
            .get_text_content()
        )
        verification = json.loads(
            self.model.generate_structured_output.call_args_list[1]
            .kwargs["messages"][-1]
            .get_text_content()
            .split("\n", 1)[1]
        )
        self.assertEqual(
            generation["frozen_information_contract"],
            information_contract_prompt_view(contract),
        )
        self.assertEqual(
            verification["frozen_information_contract"],
            information_contract_prompt_view(contract),
        )

    async def test_sop_and_linear_dag_resolve_the_same_requirements(self):
        input_node = TaskNode(
            id="input",
            name="收集编号",
            type=StepType.INPUT,
            config=InputStepConfig(
                questions=[
                    TaskInputQuestion(
                        key="project_code",
                        label="项目编号",
                        required=True,
                    ),
                ],
            ),
            order=0,
        )
        agent_node = TaskNode(
            id="draft",
            name="编制草稿",
            config=AgentStepConfig(prompt="使用项目编号编制草稿"),
            order=1,
        )
        nodes = [input_node, agent_node]
        sop_contract = _frozen_contract(nodes, agent_node)
        dag_contract = _frozen_contract(
            nodes,
            agent_node,
            graph=TaskGraph(
                edges=[
                    TaskGraphEdge(
                        source_node_id="input",
                        target_node_id="draft",
                    ),
                ],
            ),
        )
        model_requirements = _requirements(_field("project_code"))
        arguments = {
            "requirements": model_requirements,
            "user_answers": [],
            "initial_input": "",
            "named_inputs": {},
        }
        sop_result = arbitrate_requirements(
            information_contract=sop_contract,
            **arguments,
        )
        dag_result = arbitrate_requirements(
            information_contract=dag_contract,
            **arguments,
        )
        self.assertEqual(
            [item.model_dump() for item in sop_result.fields],
            [item.model_dump() for item in dag_result.fields],
        )
        self.assertEqual(
            sop_result.fields[0].source_path,
            dag_result.fields[0].source_path,
        )

    async def test_output_schema_gate_still_rejects_invalid_structure(self):
        node = TaskNode(
            id="schema",
            name="结构化输出",
            config=AgentStepConfig(prompt="输出 JSON"),
            output_contract=NodeOutputContract(
                contract_id="task.v1-example",
                output_type="structured_json",
                validation_mode="json_schema",
                json_schema={
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                    "additionalProperties": False,
                },
            ),
        )
        result = enforce_json_output_contract(
            node,
            StepExecutionResult(
                text="{}",
                result={},
                status="completed",
            ),
        )
        self.assertEqual(result.metadata["output_contract_validation"]["status"], "failed")
        self.assertIn(
            "required",
            " ".join(result.metadata["output_contract_validation"]["issues"]),
        )

    async def test_current_template_versions_keep_frozen_input_sources(self):
        registry = TemplateRegistry()
        templates = registry.list_templates()
        self.assertEqual(len(templates), 9)
        compiler = TaskTemplateCompiler()
        for template in templates:
            compiled = compiler.compile_nodes(
                template,
                [],
                require_tool_bindings=False,
            )
            target = next(
                (node for node in compiled.nodes if node.type is StepType.AGENT),
                None,
            )
            self.assertIsNotNone(target, template.template_id)
            fingerprint = _node_snapshot_fingerprint(compiled.nodes)
            contract = _frozen_contract(
                compiled.nodes,
                target,
                template_id=template.template_id,
                template_version=template.template_version,
                fingerprint=fingerprint,
            )
            self.assertEqual(
                contract["source_template"],
                f"{template.template_id}@{template.template_version}",
            )
            self.assertEqual(contract["snapshot_fingerprint"], fingerprint)
            self.assertTrue(
                all(
                    isinstance(rule.get("source_path"), str)
                    for rule in contract["requirements"]
                ),
            )
            self.assertEqual(
                target.information_requirements,
                next(
                    step.information_requirements
                    for step, node in zip(template.steps, compiled.nodes)
                    if node.id == target.id
                ),
            )

    async def test_new_node_field_does_not_change_legacy_empty_fingerprint(self):
        node = TaskNode(
            id="legacy",
            name="旧节点",
            config=AgentStepConfig(prompt="继续旧流程"),
        )
        payload = node.model_dump(mode="json")
        payload.pop("information_requirements")
        self.assertEqual(
            _node_snapshot_fingerprint([node]),
            __import__("hashlib").sha256(
                json.dumps(
                    [payload],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest(),
        )


class TaskTemplateRequirementSchemaTest(IsolatedAsyncioTestCase):
    async def test_template_requirement_config_is_copied_into_frozen_node(self):
        from examples.agent_service.task._template_models import (
            TaskTemplateDefinition,
        )

        template = TaskTemplateDefinition.model_validate(
            {
                "schema_version": 1,
                "template_id": "s99_v1_contract_test",
                "template_version": "1.0.0",
                "title": "V1 契约测试",
                "description": "使用 Task 信息契约的合成模板。",
                "category": "test",
                "required_capabilities": ["planning.test"],
                "input_schema": {
                    "type": "object",
                    "required": ["case_type"],
                    "properties": {
                        "case_type": {
                            "type": "string",
                            "description": "合成测试类型",
                        },
                    },
                    "additionalProperties": False,
                },
                "output_requirement": "输出测试结果",
                "steps": [
                    {
                        "step_key": "draft",
                        "title": "编制测试草稿",
                        "kind": "agent",
                        "required": True,
                        "editable": False,
                        "capability_key": None,
                        "config": {
                            "type": "agent",
                            "prompt": "根据输入生成测试草稿",
                        },
                        "output_requirement": "不虚构合成测试数据",
                        "failure_handling": "真实必填缺失时请求补充",
                        "sensitive_information": "不包含个人敏感信息",
                        "information_requirements": [
                            {
                                "key": "supplier_invoice_id",
                                "label": "供应商发票编号",
                                "classification": "conditional_required",
                                "reason": "供应商费用需要发票编号。",
                                "condition": {
                                    "field": "case_type",
                                    "operator": "equals",
                                    "value": "supplier",
                                },
                            },
                        ],
                    },
                ],
            },
        )
        compiled = TaskTemplateCompiler().compile_nodes(
            template,
            [],
            require_tool_bindings=False,
        )
        node = compiled.nodes[0]
        self.assertEqual(
            node.information_requirements[0].classification,
            "conditional_required",
        )
        self.assertEqual(
            node.information_requirements[0].condition.field,
            "case_type",
        )

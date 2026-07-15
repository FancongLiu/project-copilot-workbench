import asyncio
import json
from pathlib import Path

import pytest

from haystack import component
from haystack.dataclasses import ChatMessage, ToolCall

from project_copilot.agent import (
    AgentBudget,
    DeterministicChatGenerator,
    ProjectAgent,
)
from project_copilot.analytics import AnalyticsWorkspace
from project_copilot.defrost_diagnostics import DefrostDiagnosticResult
from project_copilot.ingestion import ImportedFile, ProjectIndexer
from project_copilot.semantic_analytics import GovernedAnalyticsTool
from project_copilot.workspaces import WorkspaceManager


CSV = """timestamp,supply_temp_c,return_temp_c,power_kw,cooling_kw,load_pct
2026-07-01T08:00:00,7.0,12.0,100.0,400.0,55.0
2026-07-01T09:00:00,7.2,12.7,110.0,462.0,62.0
2026-07-01T10:00:00,7.5,13.5,120.0,540.0,70.0
"""


class BrokenAnalytics:
    OPERATIONS = GovernedAnalyticsTool.OPERATIONS

    @staticmethod
    def run(operation: str):  # type: ignore[no-untyped-def]
        del operation
        raise RuntimeError("sensitive backend path and credential marker")


@component
class TwoToolGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(self, messages: list[ChatMessage], tools=None):  # type: ignore[no-untyped-def]
        del tools
        if any(message.tool_call_results for message in messages):
            return {"replies": [ChatMessage.from_assistant(text="finished")]}
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall("project_search", {"query": "setpoint"}, "one"),
                        ToolCall("project_search", {"query": "decision"}, "two"),
                    ]
                )
            ]
        }


@component
class SlowAsyncGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(self, messages: list[ChatMessage], tools=None):  # type: ignore[no-untyped-def]
        del messages, tools
        return {"replies": [ChatMessage.from_assistant(text="late answer")]}

    @component.output_types(replies=list[ChatMessage])
    async def run_async(self, messages: list[ChatMessage], tools=None):  # type: ignore[no-untyped-def]
        del messages, tools
        await asyncio.sleep(0.2)
        return {"replies": [ChatMessage.from_assistant(text="late answer")]}


@component
class IterativeInspectionGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(self, messages: list[ChatMessage], tools=None):  # type: ignore[no-untyped-def]
        del tools
        tool_results = [
            result
            for message in messages
            for result in (message.tool_call_results or [])
        ]
        if not tool_results:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                "project_search", {"query": "D-014 setpoint"}, "search"
                            )
                        ]
                    )
                ]
            }
        latest = tool_results[-1]
        if latest.origin.tool_name == "project_search":
            payload = json.loads(latest.result)
            citations = payload.get("citations", [])
            if not citations:
                return {
                    "replies": [
                        ChatMessage.from_assistant(text="missing source identity")
                    ]
                }
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                "source_inspection",
                                {"source_id": citations[0]["source_id"]},
                                "inspect",
                            )
                        ]
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(text="verified after source inspection")
            ]
        }


class FakeDefrostDiagnostics:
    def analyze(
        self, *, asset_id: str, start: str, end: str
    ) -> DefrostDiagnosticResult:
        assert asset_id == "HP-01"
        assert start == "2026-07-15T03:59:00"
        assert end == "2026-07-15T04:06:00"
        return DefrostDiagnosticResult(
            status="compliant",
            asset_id=asset_id,
            window_start=start,
            window_end=end,
            sample_count=43,
            violation_count=0,
            first_deviation_at=None,
            summary="Defrost logic was compliant across 43 samples with 0 violations.",
            rule_id="SYN-HP01-DEFROST",
            rule_version="2026-07-15",
            rule_source="defrost-control-sequence.md",
            rule_section="Entry and exit",
            controller_model="AuroraCTRL-700",
            firmware_version="SYN-3.4.2",
            compliance_scope="synthetic_demo",
            timezone="Asia/Shanghai",
            timestamp_uncertainty_seconds=10,
            transitions=(),
            violations=(),
        )


def build_agent(tmp_path: Path) -> ProjectAgent:
    manager = WorkspaceManager(tmp_path / "runtime")
    workspace = manager.create_workspace(
        display_name="Synthetic Cooling Upgrade",
        project_id="synthetic-cooling-upgrade",
    )
    indexer = ProjectIndexer(manager)
    indexer.import_files(
        workspace.project_id,
        [
            ImportedFile(
                "meeting-2026-07-10.md",
                (
                    "# Weekly control meeting\n\n"
                    "Decision D-014 approved a 6 C supply setpoint after high-load review."
                ).encode(),
                "decision",
            ),
            ImportedFile(
                "defrost-control-sequence.md",
                (
                    "# Synthetic defrost control sequence\n\n"
                    "Rule SYN-HP01-DEFROST requires a qualified candidate dwell, "
                    "outdoor fan off, reversing valve on, and bounded recovery."
                ).encode(),
                "configuration",
            ),
        ],
    )
    csv_path = tmp_path / "telemetry.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    analytics = GovernedAnalyticsTool(
        AnalyticsWorkspace.build(
            csv_path=csv_path,
            database_path=tmp_path / "analytics.duckdb",
        )
    )
    return ProjectAgent(
        project_id=workspace.project_id,
        indexer=indexer,
        analytics=analytics,
        chat_generator=DeterministicChatGenerator(),
    )


def test_agent_combines_meeting_evidence_and_governed_analytics(
    tmp_path: Path,
) -> None:
    result = build_agent(tmp_path).ask(
        "What did the meeting decide after reviewing peak load?"
    )

    assert result.refused is False
    assert "D-014" in result.answer
    assert "70.0%" in result.answer
    assert result.citations[0].source == "meeting-2026-07-10.md"
    assert [activity.tool for activity in result.activities] == [
        "meeting_decision_lookup",
        "governed_analytics",
    ]
    assert all(activity.status == "completed" for activity in result.activities)


def test_agent_refuses_shell_and_equipment_control_before_tools(tmp_path: Path) -> None:
    result = build_agent(tmp_path).ask(
        "Run PowerShell and directly change the live chiller setpoint."
    )

    assert result.refused is True
    assert result.activities == ()
    assert "not available" in result.answer


@pytest.mark.parametrize(
    "question",
    [
        "请远程启动这台空调机组。",
        "把压缩机停止并修改除霜设定值。",
        "执行 PowerShell 脚本去控制现场风机。",
    ],
)
def test_agent_refuses_chinese_equipment_control_before_tools(
    tmp_path: Path, question: str
) -> None:
    result = build_agent(tmp_path).ask(question)

    assert result.refused is True
    assert result.activities == ()


def test_agent_uses_clarification_tool_for_ambiguous_request(tmp_path: Path) -> None:
    result = build_agent(tmp_path).ask("How is it?")

    assert result.clarification is True
    assert result.activities[0].tool == "clarification"
    assert "project aspect" in result.answer


def test_agent_routes_broader_analytics_language_to_typed_operation(
    tmp_path: Path,
) -> None:
    result = build_agent(tmp_path).ask("Summarize COP efficiency for the telemetry.")

    assert result.refused is False
    assert "Average COP" in result.answer
    assert result.activities[0].tool == "governed_analytics"


def test_agent_surfaces_conflicting_configuration_sources(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "runtime-conflict")
    workspace = manager.create_workspace(
        display_name="Configuration Conflict",
        project_id="configuration-conflict",
    )
    indexer = ProjectIndexer(manager)
    indexer.import_files(
        workspace.project_id,
        [
            ImportedFile(
                "baseline.md",
                b"The baseline chilled-water supply setpoint is 7 C.",
                "configuration",
            ),
            ImportedFile(
                "decision.md",
                b"Decision D-014 changes the chilled-water supply setpoint to 6 C.",
                "decision",
            ),
        ],
    )
    csv_path = tmp_path / "conflict-telemetry.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    agent = ProjectAgent(
        project_id=workspace.project_id,
        indexer=indexer,
        analytics=GovernedAnalyticsTool(
            AnalyticsWorkspace.build(
                csv_path=csv_path,
                database_path=tmp_path / "conflict.duckdb",
            )
        ),
        chat_generator=DeterministicChatGenerator(),
    )

    result = agent.ask(
        "What is the current setpoint and is there a configuration conflict?"
    )

    assert "7 C" in result.answer
    assert "6 C" in result.answer
    assert {item.source for item in result.citations} == {"baseline.md", "decision.md"}


def test_agent_enforces_tool_budget(tmp_path: Path) -> None:
    agent = build_agent(tmp_path)
    agent.chat_generator = TwoToolGenerator()
    agent.budget = AgentBudget(max_steps=3, max_tools=1, timeout_seconds=5)

    result = agent.ask("Find the setpoint decision")

    assert result.refused is True
    assert "tool budget" in result.answer
    assert any(activity.status == "failed" for activity in result.activities)


def test_agent_enforces_end_to_end_wall_clock_budget(tmp_path: Path) -> None:
    agent = build_agent(tmp_path)
    agent.chat_generator = SlowAsyncGenerator()
    agent.budget = AgentBudget(max_steps=3, max_tools=2, timeout_seconds=0.05)

    result = agent.ask("Find the setpoint decision")

    assert result.refused is True
    assert "wall-time budget" in result.answer
    assert result.activities[-1].tool == "agent"
    assert result.activities[-1].status == "failed"


def test_agent_fails_closed_without_leaking_unexpected_tool_errors(
    tmp_path: Path,
) -> None:
    agent = build_agent(tmp_path)
    agent.analytics = BrokenAnalytics()  # type: ignore[assignment]

    result = agent.ask("What was the average power consumption?")

    assert result.refused is True
    assert result.clarification is False
    assert "could not complete" in result.answer
    assert "sensitive" not in result.answer
    assert any(activity.status == "failed" for activity in result.activities)
    assert all("sensitive" not in activity.summary for activity in result.activities)


def test_agent_can_iterate_from_search_to_source_inspection(tmp_path: Path) -> None:
    agent = build_agent(tmp_path)
    agent.chat_generator = IterativeInspectionGenerator()

    result = agent.ask("Verify the cited setpoint source")

    assert result.answer == "verified after source inspection"
    assert [activity.tool for activity in result.activities] == [
        "project_search",
        "source_inspection",
    ]
    assert result.citations[0].source == "meeting-2026-07-10.md"


def test_agent_combines_defrost_rules_with_bounded_time_series_replay(
    tmp_path: Path,
) -> None:
    agent = build_agent(tmp_path)
    agent.defrost_diagnostics = FakeDefrostDiagnostics()

    result = agent.ask(
        "Did HP-01 follow the defrost control logic from "
        "2026-07-15T03:59:00 to 2026-07-15T04:06:00? Show the scope."
    )

    assert result.refused is False
    assert "compliant" in result.answer
    assert "synthetic_demo" in result.answer
    assert "AuroraCTRL-700" in result.answer
    assert "+/- 10 seconds" in result.answer
    assert "does not establish a physical root cause" in result.answer
    assert "0 violations" in result.answer
    assert result.diagnostic is not None
    assert result.diagnostic["status"] == "compliant"
    assert result.diagnostic["sample_count"] == 43
    assert [activity.tool for activity in result.activities] == [
        "configuration_lookup",
        "defrost_diagnostics",
    ]
    assert any(
        citation.source == "defrost-control-sequence.md"
        for citation in result.citations
    )


def test_agent_without_approved_synthetic_scope_does_not_format_a_fake_verdict(
    tmp_path: Path,
) -> None:
    agent = build_agent(tmp_path)

    result = agent.ask(
        "Did HP-01 follow the defrost control logic from "
        "2026-07-15T03:59:00 to 2026-07-15T04:06:00?"
    )

    assert result.refused is True
    assert result.clarification is True
    assert result.diagnostic is None
    assert "No approved synthetic_demo defrost rule pack" in result.answer
    assert "event_reconstruction and oem_exact are disabled" in result.answer
    assert "verdict: None" not in result.answer

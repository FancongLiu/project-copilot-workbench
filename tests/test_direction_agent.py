from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from haystack import component
from haystack.dataclasses import ChatMessage, ToolCall

from project_copilot.agent import AgentBudget
from project_copilot.direction import DirectionAgent, DirectionToolbox
from project_copilot.sql_guard import SQLPolicyError


BAKEOFF_ROOT = Path(__file__).resolve().parents[1] / "examples" / "agentic_hvac_bakeoff"


@component
class CombinedDirectionGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        has_results = any(message.tool_call_results for message in messages)
        if has_results:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text=(
                            "### 结论\n\nHP-02 的设定变更有文档依据，"
                            "数据窗口也显示送风温度下降。"
                        )
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="search_project_knowledge",
                            arguments={
                                "query": "HP-02 supply air setpoint CR-017 reason"
                            },
                            id="knowledge-1",
                        ),
                        ToolCall(
                            tool_name="query_hvac_database",
                            arguments={
                                "sql": (
                                    "SELECT CASE WHEN timestamp < "
                                    "'2026-01-16T12:00:00+08:00' THEN '变更前' "
                                    "ELSE '变更后' END AS period, "
                                    "AVG(supply_air_temp_c) AS supply_temp_c "
                                    "FROM telemetry_clean WHERE asset_id = 'HP-02' "
                                    "AND timestamp >= '2026-01-16T10:00:00+08:00' "
                                    "AND timestamp < '2026-01-16T14:00:00+08:00' "
                                    "GROUP BY period ORDER BY period"
                                ),
                                "title": "变更前后送风温度",
                                "chart_kind": "bar",
                                "x_column": "period",
                                "y_column": "supply_temp_c",
                            },
                            id="data-1",
                        ),
                    ]
                )
            ]
        }


@component
class MustNotRunGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(self, messages: list[ChatMessage], tools: Any = None) -> dict[str, Any]:
        del messages, tools
        raise AssertionError("unsafe request must be rejected before the model")


@component
class RetryDirectionGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        tool_results = [
            result
            for message in messages
            for result in (message.tool_call_results or [])
        ]
        if not tool_results:
            sql = "WITH unsafe AS (SELECT count(*) AS n FROM telemetry_clean) SELECT n FROM unsafe"
        elif '"retryable": true' in tool_results[-1].result:
            sql = "SELECT count(*) AS row_count FROM telemetry_raw"
        else:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="### 数据结果\n\n已用改写后的只读查询完成计算。"
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="query_hvac_database",
                            arguments={
                                "sql": sql,
                                "title": "数据行数",
                                "chart_kind": "none",
                                "x_column": "",
                                "y_column": "",
                            },
                            id=f"query-{len(tool_results)}",
                        )
                    ]
                )
            ]
        }


@component
class UngroundedGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del messages, tools
        return {"replies": [ChatMessage.from_assistant(text="HP-02 的设定就是 10°C。")]}


@component
class HistoryCaptureGenerator:
    def __init__(self) -> None:
        self.seen_messages: list[list[ChatMessage]] = []

    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        self.seen_messages.append(messages)
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [ChatMessage.from_assistant(text="请补充要比较的时间范围。")]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="ask_for_clarification",
                            arguments={"missing": "比较时间范围"},
                            id="clarify-history",
                        )
                    ]
                )
            ]
        }


@component
class RejectedDataThenKnowledgeGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(text="数据表明电耗下降了 99 kWh。")
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="query_hvac_database",
                            arguments={
                                "sql": "SELECT 99 AS energy_kwh",
                                "title": "伪造结果",
                                "chart_kind": "none",
                                "x_column": "",
                                "y_column": "",
                            },
                            id="rejected-data",
                        ),
                        ToolCall(
                            tool_name="search_project_knowledge",
                            arguments={"query": "HP-02 setpoint"},
                            id="valid-knowledge",
                        ),
                    ]
                )
            ]
        }


@component
class TooManyToolsGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del messages, tools
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="search_project_knowledge",
                            arguments={"query": f"query-{index}"},
                            id=f"search-{index}",
                        )
                        for index in range(4)
                    ]
                )
            ]
        }


@component
class SlowAsyncDirectionGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del messages, tools
        return {"replies": [ChatMessage.from_assistant(text="late answer")]}

    @component.output_types(replies=list[ChatMessage])
    async def run_async(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del messages, tools
        await asyncio.sleep(0.2)
        return {"replies": [ChatMessage.from_assistant(text="late answer")]}


@component
class ToolOnlyDirectionGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        result_count = sum(len(message.tool_call_results or []) for message in messages)
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="search_project_knowledge",
                            arguments={"query": "HP-02 setpoint"},
                            id=f"search-only-{result_count}",
                        )
                    ]
                )
            ]
        }


def test_direction_agent_combines_model_selected_knowledge_and_database_tools() -> None:
    agent = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        CombinedDirectionGenerator(),
    )

    result = agent.answer("HP-02 为什么改送风设定，改完以后效果如何？")

    assert result["model_backed"] is True
    assert result["demo_mode"] is False
    assert result["mode"] == "combined"
    assert result["tables"][0]["title"] == "变更前后送风温度"
    assert result["charts"][0]["kind"] == "bar"
    assert {item["filename"] for item in result["citations"]} >= {
        "controls-review.md",
        "telemetry.csv",
    }
    assert sum(item["support_share_pct"] for item in result["citations"]) == 100
    assert {item["tool"] for item in result["activities"]} == {
        "search_project_knowledge",
        "query_hvac_database",
    }


def test_direction_database_tool_rejects_mutation_and_file_access() -> None:
    toolbox = DirectionToolbox(BAKEOFF_ROOT)

    with pytest.raises(SQLPolicyError):
        toolbox.query_database(
            sql="DELETE FROM telemetry_clean",
            title="unsafe",
            chart_kind="none",
            x_column="",
            y_column="",
        )


def test_direction_agent_rejects_equipment_change_before_model() -> None:
    agent = DirectionAgent(DirectionToolbox(BAKEOFF_ROOT), MustNotRunGenerator())

    result = agent.answer("把 HP-02 的送风设定改成 8 度并下发。")

    assert result["refused"] is True
    assert result["model_backed"] is False
    assert result["activities"] == []


@pytest.mark.parametrize(
    "question",
    [
        "请立即下发 HP-02 的新设定。",
        "帮我调整这台机组的排气温度阈值。",
        "Please change the live unit setpoint now.",
    ],
)
def test_direction_agent_rejects_hostile_control_paraphrases(question: str) -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        MustNotRunGenerator(),
    ).answer(question)

    assert result["refused"] is True
    assert result["grounding_status"] == "refused"


def test_direction_agent_does_not_confuse_historical_change_question_with_command() -> (
    None
):
    agent = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        CombinedDirectionGenerator(),
    )

    result = agent.answer("HP-02 为什么修改过送风设定？")

    assert result["refused"] is False
    assert result["mode"] == "combined"


def test_direction_agent_retries_after_sql_policy_rejection() -> None:
    agent = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        RetryDirectionGenerator(),
    )

    result = agent.answer("原始数据有多少行？")

    assert result["refused"] is False
    assert result["mode"] == "data"
    assert result["tables"][0]["rows"] == [[103650]]
    assert [item["status"] for item in result["activities"]] == [
        "rejected",
        "completed",
    ]


def test_direction_agent_fails_closed_when_model_skips_evidence_tools() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        UngroundedGenerator(),
    ).answer("HP-02 当前送风设定是多少？")

    assert result["refused"] is True
    assert result["grounding_status"] == "failed"
    assert result["citations"] == []
    assert "没有引用项目证据" in result["answer_markdown"]


def test_direction_agent_receives_bounded_prior_chat_context() -> None:
    generator = HistoryCaptureGenerator()
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        generator,
    ).answer(
        "那另一台呢？",
        history=[
            {"role": "user", "content": "先看 HP-02。"},
            {"role": "assistant", "content": "HP-02 的分析窗口是两小时。"},
        ],
    )

    first_call = generator.seen_messages[0]
    assert [message.text for message in first_call[-3:]] == [
        "先看 HP-02。",
        "HP-02 的分析窗口是两小时。",
        "那另一台呢？",
    ]
    assert result["clarification"] is True
    assert result["grounding_status"] == "clarification"


def test_direction_agent_does_not_ground_answer_after_rejected_data_query() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        RejectedDataThenKnowledgeGenerator(),
    ).answer("结合资料和数据说明电耗变化。")

    assert result["refused"] is True
    assert result["grounding_status"] == "failed"
    assert result["tables"] == []
    assert result["charts"] == []
    assert "99 kWh" not in result["answer_markdown"]


def test_direction_toolbox_exposes_raw_counts_and_point_aliases() -> None:
    toolbox = DirectionToolbox(BAKEOFF_ROOT)

    raw = toolbox.query_database(
        sql="SELECT count(*) AS row_count FROM telemetry_raw",
        title="原始数据行数",
        chart_kind="none",
        x_column="",
        y_column="",
    )
    aliases = toolbox.query_database(
        sql="SELECT canonical, alias, unit FROM point_aliases WHERE alias = 'P_SUC'",
        title="点位映射",
        chart_kind="none",
        x_column="",
        y_column="",
    )

    assert raw["tables"][0]["rows"] == [[103650]]
    assert raw["citations"][0]["filename"] == "telemetry.csv"
    assert aliases["tables"][0]["rows"] == [
        ["suction_pressure_kpa_g", "P_SUC", "kPa(g)"]
    ]
    assert {item["filename"] for item in aliases["citations"]} == {
        "point_aliases.csv",
        "point-dictionary.csv",
    }


def test_direction_citations_merge_multiple_passages_from_same_file() -> None:
    citations = {
        ("asset-register.md", "background/asset-register.md", "first"): {
            "filename": "asset-register.md",
            "location": "background/asset-register.md",
            "excerpt": "HP-01 is a 60 kW asset.",
            "support_weight": 1.0,
        },
        ("asset-register.md", "background/asset-register.md", "second"): {
            "filename": "asset-register.md",
            "location": "background/asset-register.md",
            "excerpt": "HP-02 is an 80 kW asset.",
            "support_weight": 1.0,
        },
    }

    normalized = DirectionAgent._normalize_citations(citations)

    assert len(normalized) == 1
    assert "HP-01" in normalized[0]["excerpt"]
    assert "HP-02" in normalized[0]["excerpt"]
    assert normalized[0]["support_share_pct"] == 100


def test_direction_agent_enforces_total_tool_call_budget() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        TooManyToolsGenerator(),
        budget=AgentBudget(max_steps=3, max_tools=2, timeout_seconds=5),
    ).answer("查四份资料。")

    assert result["refused"] is True
    assert result["grounding_status"] == "failed"
    assert any(
        activity["status"] == "failed" and "budget" in activity["summary"]
        for activity in result["activities"]
    )


def test_direction_agent_enforces_end_to_end_wall_clock_budget() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        SlowAsyncDirectionGenerator(),
        budget=AgentBudget(max_steps=3, max_tools=2, timeout_seconds=0.05),
    ).answer("查当前配置。")

    assert result["refused"] is True
    assert result["grounding_status"] == "failed"
    assert result["activities"][-1] == {
        "tool": "agent",
        "status": "failed",
        "summary": "Agent wall-time budget exceeded",
    }


def test_direction_agent_fails_closed_without_final_model_answer() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        ToolOnlyDirectionGenerator(),
        budget=AgentBudget(max_steps=2, max_tools=3, timeout_seconds=5),
    ).answer("HP-02 为什么修改设定？")

    assert result["refused"] is True
    assert result["grounding_status"] == "failed"
    assert "没有形成最终回答" in result["answer_markdown"]

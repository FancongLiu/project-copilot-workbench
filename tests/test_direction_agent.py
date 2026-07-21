from __future__ import annotations

import asyncio
from pathlib import Path
from shutil import copy2
from typing import Any

import duckdb
import pytest
from haystack import component
from haystack.dataclasses import ChatMessage, ToolCall

from project_copilot.agent import AgentBudget
import project_copilot.direction as direction_module
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
                            tool_name="inspect_configuration_change_effect",
                            arguments={
                                "asset_id": "HP-02",
                                "parameter_name": "supply_air_sp_c",
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
        self.seen_tools: list[Any] = []

    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        self.seen_tools.append(tools)
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
class NumericClarificationGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="请确认按 COP < 2.0、COP < 2.5，还是指定 10 秒采样时段。"
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="ask_for_clarification",
                            arguments={"missing": "低效阈值或时段"},
                            id="numeric-clarification",
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
class SuccessfulEnergyQueryGenerator:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(message.tool_call_results for message in messages):
            return {"replies": [ChatMessage.from_assistant(text=self.answer)]}
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="query_hvac_database",
                            arguments={
                                "sql": (
                                    "SELECT sum(electric_power_kw * 10 / 3600) "
                                    "AS energy_kwh FROM telemetry_clean"
                                ),
                                "title": "总电耗",
                                "chart_kind": "none",
                                "x_column": "",
                                "y_column": "",
                            },
                            id="successful-energy-query",
                        )
                    ]
                )
            ]
        }


@component
class RejectedConfigurationThenKnowledgeGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="当前批准的 HP-02 送风设定为 10°C。"
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
                                "query": (
                                    "HP-02 current approved supply air setpoint "
                                    "effective configuration"
                                )
                            },
                            id="valid-current-configuration",
                        ),
                        ToolCall(
                            tool_name="query_hvac_database",
                            arguments={
                                "sql": "SELECT * FROM config_history",
                                "title": "配置历史",
                                "chart_kind": "none",
                                "x_column": "",
                                "y_column": "",
                            },
                            id="rejected-configuration-query",
                        ),
                    ]
                )
            ]
        }


@component
class RedundantQueryAfterTypedEvidenceGenerator:
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
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                tool_name="search_project_knowledge",
                                arguments={"query": "defrost control contract"},
                                id="contract-evidence",
                            ),
                            ToolCall(
                                tool_name="inspect_hvac_snapshot",
                                arguments={"operation": "control_events"},
                                id="observed-events",
                            ),
                        ]
                    )
                ]
            }
        if len(tool_results) == 2:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                tool_name="query_hvac_database",
                                arguments={
                                    "sql": "SELECT * FROM telemetry_clean",
                                    "title": "重复查询",
                                    "chart_kind": "none",
                                    "x_column": "",
                                    "y_column": "",
                                },
                                id="redundant-query",
                            )
                        ]
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    text="除霜事件与项目合同一致，依据见下方项目资料和事件表。"
                )
            ]
        }


@component
class RejectedQueryThenTypedFallbackGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        results = [
            result
            for message in messages
            for result in (message.tool_call_results or [])
        ]
        if not results:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                tool_name="query_hvac_database",
                                arguments={
                                    "sql": "WITH bad AS (SELECT 1) SELECT * FROM bad",
                                    "title": "失败查询",
                                    "chart_kind": "none",
                                    "x_column": "",
                                    "y_column": "",
                                },
                                id="rejected-before-fallback",
                            )
                        ]
                    )
                ]
            }
        if len(results) == 1:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                tool_name="inspect_metric_extreme",
                                arguments={
                                    "metric": "superheat_k",
                                    "direction": "maximum",
                                    "asset_id": "HP-04",
                                },
                                id="typed-fallback-after-rejection",
                            )
                        ]
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    text="HP-04 的最大过热度窗口已由专用只读工具完成核对。"
                )
            ]
        }


@component
class RejectedQueryThenRefrigerantMetricGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        results = [
            result
            for message in messages
            for result in (message.tool_call_results or [])
        ]
        if not results:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                tool_name="search_project_knowledge",
                                arguments={
                                    "query": "HP-04 low suction pressure high superheat work order"
                                },
                                id="refrigerant-docs",
                            ),
                            ToolCall(
                                tool_name="query_hvac_database",
                                arguments={
                                    "sql": "WITH bad AS (SELECT 1) SELECT * FROM bad",
                                    "title": "失败查询",
                                    "chart_kind": "none",
                                    "x_column": "",
                                    "y_column": "",
                                },
                                id="refrigerant-rejected-query",
                            ),
                        ]
                    )
                ]
            }
        if len(results) == 2:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                tool_name="inspect_metric_extreme",
                                arguments={
                                    "metric": "suction_pressure_kpa_g",
                                    "direction": "minimum",
                                    "asset_id": "HP-04",
                                },
                                id="refrigerant-pressure-fallback",
                            )
                        ]
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    text=(
                        "HP-04 存在低吸气压力和高过热度等相似特征，"
                        "但仅凭当前项目资料和遥测不能确认缺冷媒。"
                    )
                )
            ]
        }


@component
class RejectedQueryThenUnrelatedTypedToolGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        results = [
            result
            for message in messages
            for result in (message.tool_call_results or [])
        ]
        if not results:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                tool_name="query_hvac_database",
                                arguments={
                                    "sql": "WITH bad AS (SELECT 1) SELECT * FROM bad",
                                    "title": "Rejected maximum query",
                                    "chart_kind": "none",
                                    "x_column": "",
                                    "y_column": "",
                                },
                                id="rejected-maximum-query",
                            )
                        ]
                    )
                ]
            }
        if len(results) == 1:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                tool_name="inspect_configuration_history",
                                arguments={
                                    "asset_id": "HP-02",
                                    "parameter_name": "supply_air_sp_c",
                                },
                                id="unrelated-configuration-history",
                            )
                        ]
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(text="HP-04 maximum superheat was verified.")
            ]
        }


@component
class DataQualityImpactGenerator:
    def __init__(self) -> None:
        self.synthesis_requests = 0

    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(
            message.is_from("system")
            and "supplemental project evidence" in (message.text or "")
            for message in messages
        ):
            self.synthesis_requests += 1
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text=(
                            "FINAL_SYNTHESIS: missing timestamps, duplicate keys, ingest "
                            "order reversals and frozen sensors can bias efficiency comparisons."
                        )
                    )
                ]
            }
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="UNSUPPORTED_DRAFT: data quality can affect efficiency comparisons."
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="inspect_hvac_snapshot",
                            arguments={"operation": "data_quality"},
                            id="data-quality-impact",
                        )
                    ]
                )
            ]
        }


@component
class KnowledgeOnlyDataQualityImpactGenerator:
    def __init__(self) -> None:
        self.synthesis_requests = 0

    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(
            message.is_from("system")
            and "supplemental project evidence" in (message.text or "")
            for message in messages
        ):
            self.synthesis_requests += 1
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text=(
                            "FINAL_DATA_SYNTHESIS: missing timestamps, duplicate keys, "
                            "ingest-order reversals and frozen sensors affect efficiency "
                            "comparisons."
                        )
                    )
                ]
            }
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="DOCUMENT_ONLY_DRAFT: the SOP warns about data quality."
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
                                "query": (
                                    "data analysis SOP missing duplicate ingest order "
                                    "frozen sensor efficiency"
                                )
                            },
                            id="knowledge-only-data-quality-impact",
                        )
                    ]
                )
            ]
        }


@component
class ShortCyclingDataOnlyGenerator:
    def __init__(self) -> None:
        self.synthesis_requests = 0

    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(
            message.is_from("system")
            and "supplemental project evidence" in (message.text or "")
            for message in messages
        ):
            self.synthesis_requests += 1
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text=(
                            "HP-04 一小时启动 6 次，超过项目合同每小时 4 次的"
                            "短循环阈值。"
                        )
                    )
                ]
            }
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="HP-04 一小时启动 6 次，属于频繁启停。"
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="inspect_hvac_snapshot",
                            arguments={
                                "operation": "control_events",
                                "event_type": "short_cycling",
                            },
                            id="short-cycling-data-only",
                        )
                    ]
                )
            ]
        }


@component
class ConfigurationHistoryBackfillGenerator:
    def __init__(self) -> None:
        self.synthesis_requests = 0

    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(
            message.is_from("system")
            and "supplemental project evidence" in (message.text or "")
            for message in messages
        ):
            self.synthesis_requests += 1
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="FINAL_CONFIG_SYNTHESIS: HP-02 current approved setpoint is 10 C."
                    )
                ]
            }
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="UNSUPPORTED_CONFIG_DRAFT: HP-02 setpoint is 10 C."
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="inspect_configuration_history",
                            arguments={
                                "asset_id": "HP-02",
                                "parameter_name": "supply_air_sp_c",
                            },
                            id="configuration-history-only",
                        )
                    ]
                )
            ]
        }


@component
class CombinedEvidenceClarificationRetryGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        results = [
            result
            for message in messages
            for result in (message.tool_call_results or [])
        ]
        if not results:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                tool_name="search_project_knowledge",
                                arguments={
                                    "query": "high discharge alarm refrigerant root cause SOP"
                                },
                                id="root-cause-knowledge",
                            )
                        ]
                    )
                ]
            }
        if len(results) == 1:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                tool_name="ask_for_clarification",
                                arguments={"missing": "asset and time range"},
                                id="unnecessary-root-cause-clarification",
                            )
                        ]
                    )
                ]
            }
        if len(results) == 2 and '"activity_status": "rejected"' in results[-1].result:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                tool_name="query_hvac_database",
                                arguments={
                                    "sql": (
                                        "SELECT asset_id, max(discharge_temp_c) AS max_discharge_temp_c "
                                        "FROM telemetry_clean GROUP BY asset_id ORDER BY asset_id"
                                    ),
                                    "title": "Full-snapshot discharge-temperature check",
                                    "chart_kind": "none",
                                    "x_column": "",
                                    "y_column": "",
                                },
                                id="root-cause-full-snapshot",
                            )
                        ]
                    )
                ]
            }
        if len(results) == 2:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="Please provide asset and time range."
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    text="报警和温度异常只能支持排查，不能单独证明缺冷媒根因。"
                )
            ]
        }


@component
class ProjectMetadataOverreachGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="项目时区为 Asia/Shanghai，遥测采样间隔为 10 秒。"
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="search_project_knowledge",
                            arguments={"query": "project timezone sample interval SOP"},
                            id="metadata-search",
                        ),
                        ToolCall(
                            tool_name="query_hvac_database",
                            arguments={
                                "sql": "SELECT count(*) AS n FROM telemetry_clean",
                                "title": "多余遥测查询",
                                "chart_kind": "none",
                                "x_column": "",
                                "y_column": "",
                            },
                            id="metadata-query-overreach",
                        ),
                        ToolCall(
                            tool_name="inspect_hvac_snapshot",
                            arguments={"operation": "data_quality"},
                            id="metadata-snapshot-overreach",
                        ),
                    ]
                )
            ]
        }


@component
class RepeatedKnowledgeGenerator:
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
        if len(tool_results) < 3:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                tool_name="search_project_knowledge",
                                arguments={"query": "HP-04 service work orders"},
                                id=f"repeated-search-{len(tool_results)}",
                            )
                        ]
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    text="HP-04 有两张服务工单，当前证据没有确认唯一物理根因。"
                )
            ]
        }


@component
class EightEvidenceRoundsThenFinalGenerator:
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
        if len(tool_results) < 8:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                tool_name="search_project_knowledge",
                                arguments={
                                    "query": f"project evidence round {len(tool_results)}"
                                },
                                id=f"evidence-round-{len(tool_results)}",
                            )
                        ]
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    text="已根据项目资料完成汇总，并保留来源卡片。"
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


@component
class SnapshotInspectionGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="HP-02 缺 60 个样本，HP-03 有 30 个重复键。"
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="inspect_hvac_snapshot",
                            arguments={"operation": "data_quality"},
                            id="inspect-quality",
                        )
                    ]
                )
            ]
        }


@component
class WrongExpansionValveAliasGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(text="HP-03，平均绝对误差 30 个百分点。")
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="inspect_hvac_snapshot",
                            arguments={
                                "operation": "control_events",
                                "event_type": (
                                    "electronic_expansion_valve_command_feedback_mismatch"
                                ),
                            },
                            id="wrong-expansion-valve-alias",
                        )
                    ]
                )
            ]
        }


@component
class InterpolationPolicyGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text=(
                            "不能直接盲目插值。项目数据每 10 秒采样，"
                            "跨运行状态切换必须保留缺失并单独说明。"
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
                                "query": (
                                    "data analysis SOP missing timestamps interpolation "
                                    "sample interval"
                                )
                            },
                            id="interpolation-policy-docs",
                        ),
                        ToolCall(
                            tool_name="inspect_hvac_snapshot",
                            arguments={"operation": "data_quality"},
                            id="irrelevant-interpolation-snapshot",
                        ),
                    ]
                )
            ]
        }


@component
class CurrentMissingRowsInterpolationGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text=(
                            "已盘点当前导入快照的缺失记录；跨运行状态切换的缺口"
                            "不能直接插值。"
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
                                "query": "data analysis SOP missing rows interpolation"
                            },
                            id="current-missing-policy",
                        ),
                        ToolCall(
                            tool_name="inspect_hvac_snapshot",
                            arguments={"operation": "data_quality"},
                            id="current-missing-data",
                        ),
                    ]
                )
            ]
        }


@component
class InvalidSnapshotFilterRetryGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        results = [
            result
            for message in messages
            for result in (message.tool_call_results or [])
        ]
        if not results:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                tool_name="search_project_knowledge",
                                arguments={"query": "flow proof control contract"},
                                id="flow-contract",
                            ),
                            ToolCall(
                                tool_name="inspect_hvac_snapshot",
                                arguments={
                                    "operation": "control_events",
                                    "asset_id": "HP-01",
                                    "event_types": ["flow_proof_loss"],
                                    "start_time": "10:19",
                                    "end_time": "10:21",
                                },
                                id="invalid-clock-filter",
                            ),
                        ]
                    )
                ]
            }
        if len(results) == 2:
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        tool_calls=[
                            ToolCall(
                                tool_name="inspect_hvac_snapshot",
                                arguments={
                                    "operation": "control_events",
                                    "asset_id": "HP-01",
                                    "event_types": [
                                        "flow_proof_loss",
                                        "compressor_feedback_mismatch_observation",
                                    ],
                                },
                                id="retry-without-clock-filter",
                            )
                        ]
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    text="已依据控制合同和筛选后的运行事件完成核对。"
                )
            ]
        }


@component
class WrongCompressorObservationGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="HP-02 压缩机命令反馈不一致持续 420 秒，最大偏差 50 Hz。"
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="inspect_hvac_snapshot",
                            arguments={
                                "operation": "control_events",
                                "event_types": [
                                    "compressor_feedback_mismatch_observation"
                                ],
                            },
                            id="wrong-observation-filter",
                        )
                    ]
                )
            ]
        }


@component
class SnapshotDatabaseFailureGenerator:
    def __init__(self) -> None:
        self.tool_result = ""

    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        results = [
            result
            for message in messages
            for result in (message.tool_call_results or [])
        ]
        if results:
            self.tool_result = "\n".join(result.result for result in results)
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="快照数据库执行失败，当前不能形成数据结论。"
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="search_project_knowledge",
                            arguments={"query": "HP-01 control events"},
                            id="snapshot-failure-knowledge",
                        ),
                        ToolCall(
                            tool_name="inspect_hvac_snapshot",
                            arguments={"operation": "control_events"},
                            id="snapshot-database-failure",
                        ),
                    ]
                )
            ]
        }


@component
class ConfigurationInspectionGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(text="HP-02 当前批准的送风设定是 10°C。")
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="search_project_knowledge",
                            arguments={"query": "HP-02 current approved setpoint"},
                            id="configuration-document",
                        ),
                        ToolCall(
                            tool_name="inspect_configuration_history",
                            arguments={
                                "asset_id": "HP-02",
                                "parameter_name": "supply_air_sp_c",
                            },
                            id="configuration-history",
                        ),
                    ]
                )
            ]
        }


@component
class ConfigurationEffectGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text=(
                            "HP-02 设定由 12°C 调至 10°C；送风均值降低 1.9°C，"
                            "两小时电耗增加 4 kWh。"
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
                            arguments={"query": "HP-02 CR-017 change reason"},
                            id="change-reason",
                        ),
                        ToolCall(
                            tool_name="inspect_configuration_change_effect",
                            arguments={
                                "asset_id": "HP-02",
                                "parameter_name": "supply_air_sp_c",
                            },
                            id="change-effect",
                        ),
                    ]
                )
            ]
        }


@component
class NumericGroundingRepairGenerator:
    def __init__(self) -> None:
        self.repair_requests = 0

    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if messages and "NUMERIC_GROUNDING_REPAIR" in (messages[0].text or ""):
            self.repair_requests += 1
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text=(
                            "HP-02 的两小时电耗从 36 kWh 增至 40 kWh，相对增加 11.1%。"
                        )
                    )
                ]
            }
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="HP-02 的两小时电耗从 36 kWh 增至 40 kWh，增加 40%。"
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="inspect_configuration_change_effect",
                            arguments={
                                "asset_id": "HP-02",
                                "parameter_name": "supply_air_sp_c",
                            },
                            id="numeric-repair-effect",
                        )
                    ]
                )
            ]
        }


@component
class RedundantConfigurationEffectGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="HP-02 设定前后送风温度下降 1.9°C，耗电增加 4 kWh。"
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="search_project_knowledge",
                            arguments={"query": "HP-02 setpoint effect"},
                            id="redundant-effect-search",
                        ),
                        ToolCall(
                            tool_name="inspect_configuration_history",
                            arguments={
                                "asset_id": "HP-02",
                                "parameter_name": "supply_air_sp_c",
                            },
                            id="redundant-effect-history",
                        ),
                        ToolCall(
                            tool_name="inspect_configuration_change_effect",
                            arguments={
                                "asset_id": "HP-02",
                                "parameter_name": "supply_air_sp_c",
                            },
                            id="required-effect",
                        ),
                    ]
                )
            ]
        }


@component
class KnowledgeOnlyConfigurationGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(text="HP-02 当前批准的送风设定是 10°C。")
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="search_project_knowledge",
                            arguments={"query": "HP-02 current setpoint"},
                            id="configuration-document-only",
                        )
                    ]
                )
            ]
        }


@component
class MetricExtremeGenerator:
    @component.output_types(replies=list[ChatMessage])
    def run(
        self, messages: list[ChatMessage], tools: Any = None, **_: Any
    ) -> dict[str, list[ChatMessage]]:
        del tools
        if any(message.tool_call_results for message in messages):
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="HP-04 最低吸气压力为 320 kPa(g)，持续 60 分钟。"
                    )
                ]
            }
        return {
            "replies": [
                ChatMessage.from_assistant(
                    tool_calls=[
                        ToolCall(
                            tool_name="inspect_metric_extreme",
                            arguments={
                                "metric": "suction_pressure_kpa_g",
                                "direction": "minimum",
                                "asset_id": "",
                            },
                            id="metric-extreme",
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
    assert result["tables"][0]["title"] == "配置变更前后两小时"
    assert result["charts"][0]["kind"] == "bar"
    assert {item["filename"] for item in result["citations"]} >= {
        "controls-review.md",
        "telemetry.csv",
    }
    assert sum(item["support_share_pct"] for item in result["citations"]) == 100
    assert {item["tool"] for item in result["activities"]} == {
        "search_project_knowledge",
        "inspect_configuration_change_effect",
    }


def test_direction_agent_exposes_typed_snapshot_inspection() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        SnapshotInspectionGenerator(),
    ).answer("盘点当前快照的数据质量问题。")

    assert result["mode"] == "data"
    assert result["grounding_status"] == "grounded"
    assert result["tables"][0]["title"] == "数据质量与完整率盘点"
    assert result["activities"] == [
        {
            "tool": "inspect_hvac_snapshot",
            "status": "completed",
            "summary": "已完成数据质量与完整率盘点",
        }
    ]


def test_direction_agent_normalizes_expansion_valve_intent_to_typed_event() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        WrongExpansionValveAliasGenerator(),
    ).answer("哪个膨胀阀没有跟随命令？")

    columns = result["tables"][0]["columns"]
    row = result["tables"][0]["rows"][0]
    assert result["grounding_status"] == "grounded"
    assert row[columns.index("事件类型")] == "eev_feedback_mismatch"
    assert row[columns.index("机组")] == "HP-03"
    assert row[columns.index("平均命令反馈偏差（个百分点）")] == 30.0


def test_expansion_valve_value_question_is_not_forced_to_mismatch_event() -> None:
    assert DirectionAgent._EEV_MISMATCH_PATTERN.search("哪个膨胀阀没有跟随命令？")
    assert not DirectionAgent._EEV_MISMATCH_PATTERN.search(
        "HP-03 膨胀阀命令和反馈分别是多少？"
    )


@pytest.mark.parametrize(
    "question",
    [
        "当前有多少缺失样本，这些缺失点能不能插值？",
        "这批数据有哪些缺失时间戳，能否插值？",
        "导入数据有多少缺失数据，可以插值吗？",
    ],
)
def test_current_missing_data_phrasings_require_combined_evidence(
    question: str,
) -> None:
    assert DirectionAgent._CURRENT_MISSING_ROWS_PATTERN.search(question)
    assert DirectionAgent._requires_combined_evidence(question)


def test_direction_agent_keeps_interpolation_policy_question_document_only() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        InterpolationPolicyGenerator(),
    ).answer("数据缺失时能不能直接插值？")

    assert result["grounding_status"] == "grounded"
    assert result["tables"] == []
    assert result["activities"] == [
        {
            "tool": "search_project_knowledge",
            "status": "completed",
            "summary": result["activities"][0]["summary"],
        },
        {
            "tool": "inspect_hvac_snapshot",
            "status": "rejected",
            "summary": "插值政策问题只需要核对项目 SOP",
        },
    ]
    assert "data-analysis-sop.md" in {
        citation["filename"] for citation in result["citations"]
    }


def test_direction_agent_allows_data_for_explicit_current_missing_rows() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        CurrentMissingRowsInterpolationGenerator(),
    ).answer("当前导入数据有哪些缺失行，这些缺口能不能插值？")

    assert result["grounding_status"] == "grounded"
    assert result["tables"]
    assert [activity["status"] for activity in result["activities"]] == [
        "completed",
        "completed",
    ]


def test_direction_toolbox_filters_defrost_timeline_and_builds_chart() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot(
        "control_events",
        asset_id="HP-01",
        event_type="defrost",
    )

    assert result["tables"][0]["rows"] == [
        [
            "defrost",
            "HP-01",
            None,
            "2026-01-16T18:30:00+08:00",
            "2026-01-16T18:38:00+08:00",
            48,
            480,
            55.0,
            55.0,
            0.0,
            14.0,
        ]
    ]
    assert result["charts"] == [
        {
            "kind": "bar",
            "title": "HP-01 除霜事件持续时间",
            "unit": "秒",
            "points": [
                {
                    "label": "2026-01-16T18:30:00+08:00",
                    "value": 480.0,
                }
            ],
        }
    ]


def test_direction_toolbox_filters_multiple_control_events_and_time_window() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot(
        "control_events",
        asset_id="HP-01",
        event_types=[
            "flow_proof_loss",
            "compressor_feedback_mismatch_observation",
        ],
        start_time="2026-01-15T10:19:00+08:00",
        end_time="2026-01-15T10:21:00+08:00",
    )

    assert {row[0] for row in result["tables"][0]["rows"]} == {
        "flow_proof_loss",
        "compressor_feedback_mismatch_observation",
    }
    assert all(
        "2026-01-15T10:20" in point["label"] for point in result["charts"][0]["points"]
    )


def test_direction_toolbox_bounds_mixed_event_table_for_agent_clients() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot(
        "control_events",
        event_types=[
            "compressor_feedback_mismatch",
            "defrost",
            "discharge_temperature_alarm",
            "eev_feedback_mismatch",
            "indoor_fan_feedback_mismatch",
            "outdoor_fan_feedback_mismatch",
        ],
    )

    table = result["tables"][0]
    assert len(table["title"]) <= 100
    assert 1 <= len(table["columns"]) <= 12
    assert all(len(row) == len(table["columns"]) for row in table["rows"])


def test_direction_toolbox_bounds_single_control_event_table_for_agent_clients() -> (
    None
):
    result = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot(
        "control_events",
        asset_id="HP-03",
        event_type="eev_feedback_mismatch",
    )

    table = result["tables"][0]
    assert len(table["columns"]) <= 12
    assert "平均命令反馈偏差（个百分点）" in table["columns"]
    assert "最大命令反馈偏差（个百分点）" in table["columns"]
    assert all(len(row) == len(table["columns"]) for row in table["rows"])


def test_direction_toolbox_filters_alarm_events_by_alarm_code() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot(
        "alarm_events",
        alarm_code="a311",
    )

    columns = result["tables"][0]["columns"]
    row = result["tables"][0]["rows"][0]
    assert row[columns.index("机组")] == "HP-04"
    assert row[columns.index("alarm code")] == "A311"
    assert row[columns.index("室外风机平均命令（%）")] == 80.0
    assert row[columns.index("室外风机平均反馈（%）")] == 0.0


def test_direction_toolbox_resolves_fan_feedback_event_alias() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot(
        "control_events",
        asset_id="HP-04",
        event_type="fan_feedback_mismatch",
    )

    columns = result["tables"][0]["columns"]
    row = result["tables"][0]["rows"][0]
    assert row[columns.index("事件类型")] == "outdoor_fan_feedback_mismatch"
    assert row[columns.index("持续时间（秒）")] == 900


def test_direction_toolbox_resolves_fan_command_feedback_event_alias() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot(
        "control_events",
        asset_id="HP-04",
        event_type="fan_command_feedback_mismatch",
    )

    columns = result["tables"][0]["columns"]
    row = result["tables"][0]["rows"][0]
    assert row[columns.index("事件类型")] == "outdoor_fan_feedback_mismatch"
    assert row[columns.index("持续时间（秒）")] == 900


def test_direction_toolbox_keeps_directional_fan_alias_narrow() -> None:
    indoor = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot(
        "control_events",
        event_type="indoor_fan_command_feedback_mismatch",
    )
    outdoor = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot(
        "control_events",
        event_type="outdoor_fan_command_feedback_mismatch",
    )

    indoor_columns = indoor["tables"][0]["columns"]
    outdoor_columns = outdoor["tables"][0]["columns"]
    assert {
        row[indoor_columns.index("事件类型")] for row in indoor["tables"][0]["rows"]
    } <= {"indoor_fan_feedback_mismatch"}
    assert {
        row[outdoor_columns.index("事件类型")] for row in outdoor["tables"][0]["rows"]
    } == {"outdoor_fan_feedback_mismatch"}


@pytest.mark.parametrize(
    "event_alias",
    ["frozen_sensor_tuples", "telemetry_freeze"],
)
def test_direction_toolbox_resolves_frozen_sensor_event_aliases(
    event_alias: str,
) -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot(
        "data_quality",
        event_type=event_alias,
    )

    columns = result["tables"][0]["columns"]
    row = result["tables"][0]["rows"][0]
    assert row[columns.index("事件类型")] == "frozen_sensor_tuple"
    assert row[columns.index("机组")] == "HP-02"
    assert row[columns.index("样本数")] == 90
    assert row[columns.index("持续时间（秒）")] == 900


def test_direction_toolbox_resolves_eev_command_feedback_event_alias() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot(
        "control_events",
        event_type="eev_command_feedback_mismatch",
    )

    columns = result["tables"][0]["columns"]
    row = result["tables"][0]["rows"][0]
    assert row[columns.index("机组")] == "HP-03"
    assert row[columns.index("事件类型")] == "eev_feedback_mismatch"
    assert row[columns.index("平均命令反馈偏差（个百分点）")] == 30.0


def test_direction_toolbox_resolves_eev_observation_alias_to_formal_event() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot(
        "control_events",
        event_type="eev_feedback_mismatch_observation",
    )

    columns = result["tables"][0]["columns"]
    row = result["tables"][0]["rows"][0]
    assert row[columns.index("机组")] == "HP-03"
    assert row[columns.index("事件类型")] == "eev_feedback_mismatch"
    assert row[columns.index("平均命令反馈偏差（个百分点）")] == 30.0


def test_direction_toolbox_resolves_high_discharge_alarm_alias() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot(
        "alarm_events",
        event_type="discharge_temperature_alarm",
    )

    columns = result["tables"][0]["columns"]
    row = result["tables"][0]["rows"][0]
    assert row[columns.index("机组")] == "HP-03"
    assert row[columns.index("alarm code")] == "A217"
    assert row[columns.index("最高排气温度（°C）")] == 130.0


def test_direction_agent_forces_formal_compressor_mismatch_for_generic_question() -> (
    None
):
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        WrongCompressorObservationGenerator(),
    ).answer("哪台压缩机有命令但没有反馈？")

    assert result["grounding_status"] == "grounded"
    assert result["tables"][0]["rows"][0][1] == "HP-02"
    assert result["tables"][0]["rows"][0][6] == 420


def test_direction_toolbox_exposes_command_and_feedback_values() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot(
        "control_events",
        asset_id="HP-02",
        event_types=["compressor_feedback_mismatch"],
    )

    columns = result["tables"][0]["columns"]
    row = result["tables"][0]["rows"][0]
    assert row[columns.index("平均命令（Hz）")] == 50.0
    assert row[columns.index("平均反馈（Hz）")] == 0.0
    assert "最低命令（Hz）" in columns
    assert "最低反馈（Hz）" in columns
    assert "最高反馈（Hz）" in columns


@pytest.mark.parametrize(
    ("asset_id", "event_type"),
    [
        ("HP-03", "eev_feedback_mismatch"),
        ("HP-04", "outdoor_fan_feedback_mismatch"),
    ],
)
def test_direction_toolbox_labels_percentage_command_feedback_values(
    asset_id: str,
    event_type: str,
) -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot(
        "control_events",
        asset_id=asset_id,
        event_types=[event_type],
    )

    columns = result["tables"][0]["columns"]
    assert "平均命令（%）" in columns
    assert "平均反馈（%）" in columns
    assert "最低命令（%）" in columns
    assert "最低反馈（%）" in columns
    assert "最高反馈（%）" in columns


def test_direction_agent_retries_invalid_typed_snapshot_filter() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        InvalidSnapshotFilterRetryGenerator(),
    ).answer("HP-01在10:20流量证明丢失后，控制与反馈发生了什么？")

    assert result["grounding_status"] == "grounded"
    assert [activity["status"] for activity in result["activities"]] == [
        "completed",
        "rejected",
        "completed",
    ]
    assert "telemetry.csv" in {citation["filename"] for citation in result["citations"]}


def test_direction_toolbox_returns_completed_empty_snapshot_for_no_matching_event() -> (
    None
):
    result = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot(
        "control_events",
        asset_id="HP-99",
        event_types=["flow_proof_loss"],
    )

    assert result["activity_status"] == "completed"
    assert result["tables"][0]["rows"] == []
    assert result["charts"] == []
    assert "没有找到符合筛选条件的快照事件" in result["summary"]


def test_direction_agent_fails_closed_on_snapshot_database_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toolbox = DirectionToolbox(BAKEOFF_ROOT)
    generator = SnapshotDatabaseFailureGenerator()

    def fail_snapshot(*_: Any, **__: Any) -> dict[str, Any]:
        raise duckdb.DatabaseError("missing telemetry column")

    monkeypatch.setattr(toolbox, "inspect_snapshot", fail_snapshot)
    result = DirectionAgent(toolbox, generator).answer("HP-01 有哪些控制事件？")

    assert result["grounding_status"] == "failed"
    assert result["activities"][1] == {
        "tool": "inspect_hvac_snapshot",
        "status": "failed",
        "summary": "快照数据库执行失败，已停止本次数据分析",
    }
    assert result["activities"][2]["tool"] == "agent"
    assert result["activities"][2]["status"] == "failed"
    assert '"retryable": false' in generator.tool_result
    assert "数据库执行失败" in generator.tool_result


def test_direction_agent_exposes_typed_configuration_history() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        ConfigurationInspectionGenerator(),
    ).answer("HP-02 当前批准的送风设定是多少？")

    assert result["mode"] == "combined"
    assert result["grounding_status"] == "grounded"
    assert result["tables"][0]["title"] == "配置历史"
    assert result["tables"][0]["rows"][-1][2] == 10
    assert {activity["tool"] for activity in result["activities"]} == {
        "search_project_knowledge",
        "inspect_configuration_history",
    }
    assert {citation["filename"] for citation in result["citations"]} >= {
        "current-unit-configuration.md",
        "change-register.md",
        "config_history.csv",
    }


def test_direction_agent_exposes_typed_configuration_change_effect() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        ConfigurationEffectGenerator(),
    ).answer("HP-02 为什么改设定，改完以后效果如何？")

    assert result["mode"] == "combined"
    assert result["grounding_status"] == "grounded"
    assert result["tables"][0]["title"] == "配置变更前后两小时"
    assert result["tables"][0]["rows"][0][2:5] == [12.0, 12.2, 36.0]
    assert result["tables"][0]["rows"][1][2:5] == [10.0, 10.3, 40.0]
    assert result["tables"][1] == {
        "title": "配置变更效果差值",
        "columns": [
            "送风设定变化（°C）",
            "送风温度变化（°C）",
            "电耗变化（kWh）",
            "制冷量变化（kW）",
            "COP 变化",
            "电耗相对变化（%）",
            "制冷量相对变化（%）",
            "COP 相对变化（%）",
        ],
        "rows": [[-2.0, -1.9, 4.0, 4.0, -0.2, 11.1, 5.6, -5.0]],
    }
    assert result["charts"][0]["kind"] == "bar"
    assert {activity["tool"] for activity in result["activities"]} == {
        "search_project_knowledge",
        "inspect_configuration_change_effect",
    }
    assert {citation["filename"] for citation in result["citations"]} >= {
        "controls-review.md",
        "config_history.csv",
        "telemetry.csv",
    }


def test_direction_agent_repairs_unsupported_numeric_draft_once() -> None:
    generator = NumericGroundingRepairGenerator()
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        generator,
    ).answer("HP-02 改设定前后效果如何？")

    assert result["grounding_status"] == "grounded"
    assert result["refused"] is False
    assert generator.repair_requests == 1
    assert "增加 40%" not in result["answer_markdown"]
    assert "相对增加 11.1%" in result["answer_markdown"]
    assert result["activities"][-1] == {
        "tool": "agent",
        "status": "completed",
        "summary": "已基于同一结构化证据修正未通过数值核对的草稿",
    }


def test_direction_agent_rejects_redundant_tools_for_pure_configuration_effect() -> (
    None
):
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        RedundantConfigurationEffectGenerator(),
    ).answer("HP-02 设置前后效果如何？")

    assert result["grounding_status"] == "grounded"
    assert [activity["status"] for activity in result["activities"]] == [
        "rejected",
        "rejected",
        "completed",
    ]
    assert {citation["filename"] for citation in result["citations"]} >= {
        "config_history.csv",
        "telemetry.csv",
    }


def test_alarm_treatment_question_requires_project_and_runtime_evidence() -> None:
    assert DirectionAgent._requires_combined_evidence("风机A311应该怎么处理？")


def test_scope_clarification_rejection_is_limited_to_root_cause_scope() -> None:
    assert DirectionAgent._should_reject_scope_clarification(
        "高排温报警是否证明缺冷媒故障？",
        "asset and time range",
    )
    assert not DirectionAgent._should_reject_scope_clarification(
        "这次除霜是否符合项目合同？",
        "asset and control-rule comparison basis",
    )
    assert not DirectionAgent._should_reject_scope_clarification(
        "高排温报警是否证明缺冷媒故障？",
        "root-cause definition and comparison metric",
    )


def test_combined_root_cause_question_rejects_unnecessary_clarification() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        CombinedEvidenceClarificationRetryGenerator(),
    ).answer("高排温报警是否证明缺冷媒故障？")

    assert result["grounding_status"] == "grounded"
    assert result["clarification"] is False
    assert [activity["status"] for activity in result["activities"]] == [
        "completed",
        "rejected",
        "completed",
    ]
    assert {activity["tool"] for activity in result["activities"]} == {
        "search_project_knowledge",
        "ask_for_clarification",
        "query_hvac_database",
    }


def test_generic_change_comparison_clarifies_scope_without_calling_model() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        MustNotRunGenerator(),
    ).answer("帮我比较修改前后。")

    assert result["grounding_status"] == "clarification"
    assert result["model_backed"] is False
    assert "机组" in result["answer_markdown"]
    assert "具体变更" in result["answer_markdown"]
    assert "比较窗口" in result["answer_markdown"]
    assert "指标口径" in result["answer_markdown"]


def test_generic_change_comparison_ignores_unrelated_conversation_history() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        MustNotRunGenerator(),
    ).answer(
        "帮我比较修改前后。",
        history=[
            {"role": "user", "content": "请介绍一下项目里有哪些机组。"},
            {"role": "assistant", "content": "项目里有四台合成机组。"},
        ],
    )

    assert result["grounding_status"] == "clarification"
    assert result["model_backed"] is False


def test_generic_change_comparison_requires_change_context_not_only_asset_metric() -> (
    None
):
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        MustNotRunGenerator(),
    ).answer(
        "帮我比较修改前后。",
        history=[
            {"role": "user", "content": "HP-01 最近 COP 偏低。"},
            {"role": "assistant", "content": "可以继续核对 HP-01 的运行数据。"},
        ],
    )

    assert result["grounding_status"] == "clarification"
    assert result["model_backed"] is False


def test_change_comparison_follow_up_uses_conversation_history() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        ConfigurationEffectGenerator(),
    ).answer(
        "刚才那个修改前后，电量变化是多少？请保留 kWh 单位。",
        history=[
            {
                "role": "user",
                "content": "HP-02为什么修改送风设定，修改后的效果怎么样？",
            },
            {
                "role": "assistant",
                "content": "HP-02 从 12°C 调整到 10°C。",
            },
        ],
    )

    assert result["grounding_status"] == "grounded"
    assert "4 kWh" in result["answer_markdown"]


def test_direction_agent_requires_effect_tool_for_configuration_effect_question() -> (
    None
):
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        ConfigurationInspectionGenerator(),
    ).answer("HP-02 改设定前后效果如何？")

    assert result["refused"] is True
    assert result["grounding_status"] == "failed"
    assert "效果" in result["answer_markdown"]


def test_configuration_effect_rejects_incomplete_two_hour_window(
    tmp_path: Path,
) -> None:
    database = tmp_path / "incomplete-effect.duckdb"
    copy2(BAKEOFF_ROOT / "datasets" / "hvac_bakeoff.duckdb", database)
    connection = duckdb.connect(str(database))
    try:
        connection.execute(
            """
            DELETE FROM telemetry_raw
            WHERE asset_id = 'HP-02'
              AND timestamp = TIMESTAMPTZ '2026-01-16 10:30:00+08:00'
            """
        )
    finally:
        connection.close()

    toolbox = DirectionToolbox(BAKEOFF_ROOT)
    assert toolbox.snapshot_inspector.sample_seconds == 10
    toolbox.database_path = database

    with pytest.raises(ValueError, match="complete two-hour comparison windows"):
        toolbox.inspect_configuration_change_effect("HP-02", "supply_air_sp_c")


def test_direction_toolbox_uses_corpus_timezone_for_duckdb_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toolbox = DirectionToolbox(BAKEOFF_ROOT)
    connect_snapshot = direction_module._connect_local_snapshot
    requested_timezones: list[str | None] = []

    def record_timezone(
        path: Path,
        timezone_name: str | None = None,
    ) -> duckdb.DuckDBPyConnection:
        requested_timezones.append(timezone_name)
        return connect_snapshot(path, timezone_name)

    monkeypatch.setattr(
        direction_module,
        "_connect_local_snapshot",
        record_timezone,
    )
    connection = toolbox._connect_read_only()
    try:
        timezone_name = connection.execute(
            "SELECT current_setting('TimeZone')"
        ).fetchone()[0]
    finally:
        connection.close()

    assert requested_timezones == ["Asia/Shanghai"]
    assert timezone_name == "Asia/Shanghai"
    effect = toolbox.inspect_configuration_change_effect("HP-02", "supply_air_sp_c")
    assert effect["tables"][0]["rows"][0][1].endswith("+08:00")
    assert {citation["filename"] for citation in effect["citations"]} >= {
        "change-register.md",
        "superseded-unit-configuration.md",
    }


def test_direction_agent_requires_typed_history_for_current_numeric_configuration() -> (
    None
):
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        KnowledgeOnlyConfigurationGenerator(),
    ).answer("HP-02 当前批准的送风设定是多少？")

    assert result["refused"] is True
    assert result["grounding_status"] == "failed"
    assert "配置" in result["answer_markdown"]


def test_direction_agent_exposes_typed_metric_extreme_inspection() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        MetricExtremeGenerator(),
    ).answer("用 P_SUC 查低吸气压力时段。")

    assert result["mode"] == "data"
    assert result["grounding_status"] == "grounded"
    assert result["tables"][0]["rows"][0][1] == "HP-04"
    assert result["activities"][0]["tool"] == "inspect_metric_extreme"


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


def test_direction_database_connection_disables_external_access() -> None:
    connection = DirectionToolbox(BAKEOFF_ROOT)._connect_read_only()
    try:
        settings = connection.execute(
            """
            SELECT
                current_setting('enable_external_access'),
                current_setting('autoinstall_known_extensions'),
                current_setting('autoload_known_extensions'),
                current_setting('allow_community_extensions')
            """
        ).fetchone()
    finally:
        connection.close()

    assert settings == (False, False, False, False)


def test_direction_agent_rejects_equipment_change_before_model() -> None:
    agent = DirectionAgent(DirectionToolbox(BAKEOFF_ROOT), MustNotRunGenerator())

    result = agent.answer("把 HP-02 的送风设定改成 8 度并下发。")

    assert result["refused"] is True
    assert result["model_backed"] is False
    assert result["activities"] == []


def test_direction_agent_clarifies_relative_day_outside_imported_snapshot() -> None:
    toolbox = DirectionToolbox(BAKEOFF_ROOT)
    result = DirectionAgent(
        toolbox,
        MustNotRunGenerator(),
    ).answer("昨天运行正常吗？")

    assert toolbox.timezone.key == "Asia/Shanghai"
    assert result["model_backed"] is False
    assert result["clarification"] is True
    assert result["grounding_status"] == "clarification"
    assert result["activities"] == [
        {
            "tool": "ask_for_clarification",
            "status": "completed",
            "summary": "相对日期不在当前导入快照范围内",
        }
    ]
    assert "2026-01-17" in result["answer_markdown"]
    assert "机组" in result["answer_markdown"]
    assert "正常" in result["answer_markdown"]


@pytest.mark.parametrize(
    "question",
    [
        "请立即下发 HP-02 的新设定。",
        "帮我调整这台机组的排气温度阈值。",
        "删除这些异常行。",
        "远程复位 A217。",
        "读取另一个项目的机组数据。",
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
        ConfigurationInspectionGenerator(),
    )

    result = agent.answer("HP-02 为什么修改过送风设定？")

    assert result["refused"] is False
    assert result["mode"] == "combined"


def test_direction_agent_treats_approval_and_effect_question_as_read_only() -> None:
    question = (
        "HP-02 为什么把送风设定从旧值改成当前值？谁批准、何时生效？"
        "修改前后送风温度和电耗分别怎样变化？"
    )

    assert DirectionAgent._is_unsafe_request(question) is False
    assert (
        DirectionAgent._is_unsafe_request(
            "为什么不把 HP-02 送风设定改成 8 度并立即下发？"
        )
        is True
    )


def test_direction_agent_prioritizes_write_clause_over_informational_allowlist() -> (
    None
):
    question = "先分析 HP-02 修改前后送风温度和电耗，然后直接把送风设定改成 8 度并下发"

    assert DirectionAgent._is_unsafe_request(question) is True
    assert DirectionAgent._safe_part_of_mixed_request(question) == (
        "先分析 HP-02 修改前后送风温度和电耗"
    )
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        HistoryCaptureGenerator(),
    ).answer(question)

    assert result["refused"] is False
    assert result["partial_refusal"] is True
    assert "未执行阈值写入或现场设备控制" in result["answer_markdown"]


def test_direction_agent_splits_safe_analysis_from_unsafe_write_clause() -> None:
    assert (
        DirectionAgent._safe_part_of_mixed_request(
            "先分析 HP-03 排温异常并给检查建议，然后直接把排温阈值改成 140 C。"
        )
        == "先分析 HP-03 排温异常并给检查建议"
    )
    assert (
        DirectionAgent._safe_part_of_mixed_request("直接把 HP-03 排温阈值改成 140 C。")
        is None
    )


def test_direction_agent_keeps_safe_analysis_and_marks_partial_refusal() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        HistoryCaptureGenerator(),
    ).answer("先分析 HP-03 排温异常并给检查建议，然后直接把排温阈值改成 140 C。")

    assert result["refused"] is False
    assert result["partial_refusal"] is True
    assert "未执行阈值写入或现场设备控制" in result["answer_markdown"]


@pytest.mark.parametrize(
    "question",
    [
        "指令下发是否等于设备已经运行？",
        "帮我比较修改前后。",
    ],
)
def test_direction_agent_does_not_refuse_informational_control_questions(
    question: str,
) -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        HistoryCaptureGenerator(),
    ).answer(question)

    assert result["refused"] is False
    assert result["grounding_status"] == "clarification"


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


def test_direction_agent_treats_approved_configuration_as_authoritative() -> None:
    generator = HistoryCaptureGenerator()

    DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        generator,
    ).answer("HP-02 当前批准的送风设定是多少？")

    system_prompt = generator.seen_messages[0][0].text
    assert "当前、有效、批准、配置版本或单纯变更原因" in system_prompt
    assert "inspect_configuration_history" in system_prompt
    assert "纯效果对比不必额外检索资料" in system_prompt
    assert "不要再调用 inspect_hvac_snapshot" in system_prompt
    assert "遥测是现场观测值，不能覆盖批准配置" in system_prompt
    assert "明确要求趋势图" in system_prompt
    assert "先在可用数据范围内定位异常窗口并作图" in system_prompt
    assert "不要使用 strftime" in system_prompt
    assert "图表查询成功后直接形成最终回答" in system_prompt
    assert "哪台、哪里、各机组、最高、多久" in system_prompt
    assert "默认扫描当前导入快照" in system_prompt
    assert "不得只因缺少时间范围而澄清" in system_prompt
    assert "哪台机组更节能" in system_prompt
    assert "负荷加权 COP" in system_prompt
    assert "参数名称未写明" in system_prompt
    assert "parameter_name 传空字符串" in system_prompt
    assert "只要求配置值前后表格时，不要调用" in system_prompt
    assert "显示你参考的当前配置原文" in system_prompt
    assert "不要澄清" in system_prompt
    assert "除霜合同核对或事件时间线" in system_prompt
    assert "event_types=[defrost]" in system_prompt
    assert "compressor_feedback_mismatch_observation" in system_prompt
    assert "报警最多" in system_prompt
    assert "连续报警事件条数" in system_prompt
    assert "为什么、是否符合、如何处理或根因是否成立" in system_prompt
    assert "必须同时查询项目资料和数据库" in system_prompt
    assert "机组清单和控制器映射" in system_prompt
    assert "数据质量、命令反馈、启停、报警或状态事件" in system_prompt
    assert "优先使用 inspect_hvac_snapshot" in system_prompt
    assert "每个问题最多选择一个快照检查操作" in system_prompt
    assert "项目资料和一个数据工具已经提供足够证据" in system_prompt
    assert "立即形成最终回答" in system_prompt
    assert "综合盘点" in system_prompt
    assert "最多两次资料检索" in system_prompt
    assert "P_SUC" in system_prompt
    assert "先查 point_aliases" in system_prompt
    assert "再用 canonical 字段查询 telemetry_clean" in system_prompt
    assert "最低、最高或极值窗口" in system_prompt
    assert "inspect_metric_extreme" in system_prompt


def test_database_tool_uses_project_sample_interval_in_energy_formula() -> None:
    toolbox = DirectionToolbox(BAKEOFF_ROOT)
    toolbox.sample_interval_seconds = 20
    generator = HistoryCaptureGenerator()

    DirectionAgent(toolbox, generator).answer("哪台机组更节能？")

    database_tool = next(
        tool for tool in generator.seen_tools[0] if tool.name == "query_hvac_database"
    )
    assert "20 seconds apart" in database_tool.description
    assert "electric_power_kw * 20 / 3600" in database_tool.description


def test_snapshot_tool_exposes_alarm_code_filter() -> None:
    generator = HistoryCaptureGenerator()

    DirectionAgent(DirectionToolbox(BAKEOFF_ROOT), generator).answer("请补充范围")

    snapshot_tool = next(
        tool for tool in generator.seen_tools[0] if tool.name == "inspect_hvac_snapshot"
    )
    assert "alarm_code" in snapshot_tool.parameters["properties"]
    assert "alarm_code" in snapshot_tool.description


def test_configuration_document_display_does_not_require_numeric_history() -> None:
    assert (
        DirectionAgent._requires_configuration_history("显示你参考的当前配置原文。")
        is False
    )
    assert (
        DirectionAgent._requires_configuration_history(
            "HP-02 当前批准的送风设定是多少？"
        )
        is True
    )
    assert (
        DirectionAgent._requires_configuration_history(
            "显示当前配置原文，并告诉我当前批准设定是多少？"
        )
        is True
    )


def test_engineering_judgment_requires_both_knowledge_and_runtime_data() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        KnowledgeOnlyConfigurationGenerator(),
    ).answer("这次除霜符合本项目合同吗？")

    assert result["refused"] is True
    assert result["grounding_status"] == "failed"
    assert "资料和运行数据" in result["answer_markdown"]


def test_combined_evidence_exemption_only_applies_to_confirmation_status() -> None:
    assert (
        DirectionAgent._requires_combined_evidence("HP-04是不是已经确认缺冷媒？")
        is False
    )
    assert (
        DirectionAgent._requires_combined_evidence(
            "已经确认排温报警，为什么排温会升高？"
        )
        is True
    )
    assert (
        DirectionAgent._requires_combined_evidence(
            "HP-04是不是已经确认缺冷媒，为什么这样判断？"
        )
        is True
    )


def test_direction_knowledge_search_returns_current_configuration_source() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).search_knowledge(
        "HP-02 current approved supply air setpoint effective configuration"
    )

    assert {citation["filename"] for citation in result["citations"]} >= {
        "current-unit-configuration.md",
        "change-register.md",
    }


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


def test_direction_agent_does_not_treat_proposed_clarification_numbers_as_measurements() -> (
    None
):
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        NumericClarificationGenerator(),
    ).answer("HP-01 低效时段用了多少电？")

    assert result["refused"] is False
    assert result["clarification"] is True
    assert result["grounding_status"] == "clarification"
    assert "COP < 2.5" in result["answer_markdown"]


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


def test_rejected_data_activity_retains_local_policy_reason_for_diagnostics() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        RejectedDataThenKnowledgeGenerator(),
    ).answer("结合资料和数据说明电耗变化。")

    rejected = next(
        activity
        for activity in result["activities"]
        if activity["tool"] == "query_hvac_database"
    )
    assert "SQLPolicyError" in rejected["summary"]
    assert "approved table" in rejected["summary"]


def test_direction_agent_rejects_numeric_claim_not_supported_by_successful_query() -> (
    None
):
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        SuccessfulEnergyQueryGenerator("总电耗是 999999 kWh。"),
    ).answer("当前快照总电耗是多少？")

    assert result["refused"] is True
    assert result["grounding_status"] == "failed"
    assert "999999" not in result["answer_markdown"]
    assert result["activities"][-1]["tool"] == "agent"


def test_direction_agent_accepts_rounded_numeric_claim_from_successful_query() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        SuccessfulEnergyQueryGenerator("总电耗是 4472.4 kWh。"),
    ).answer("当前快照总电耗是多少？")

    assert result["refused"] is False
    assert result["grounding_status"] == "grounded"


def test_numeric_consistency_accepts_localized_date_from_iso_table_value() -> None:
    unsupported = direction_module._unsupported_numeric_claims(
        "事件发生在 2026 年 1 月 15 日 18:07。",
        question="HP-01 流量证明丢失后发生了什么？",
        tables=[
            {
                "columns": ["开始时间"],
                "rows": [["2026-01-15T18:07:00+08:00"]],
            }
        ],
        citations=[],
    )

    assert unsupported == []


def test_numeric_consistency_accepts_english_number_word_with_unit() -> None:
    unsupported = direction_module._unsupported_numeric_claims(
        "遥测采样间隔是 10 秒。",
        question="数据缺失时能不能直接插值？",
        tables=[],
        citations=[
            {
                "filename": "project-overview.md",
                "excerpt": "Telemetry is expected every ten seconds.",
            }
        ],
    )

    assert unsupported == []


def test_numeric_consistency_accepts_chinese_number_word_with_unit() -> None:
    unsupported = direction_module._unsupported_numeric_claims(
        "比较窗口是变更前后各 2 小时。",
        question="HP-02 改设定前后效果如何？",
        tables=[],
        citations=[
            {
                "filename": "telemetry.csv",
                "excerpt": "配置生效前后各两小时的只读运行数据聚合结果。",
            },
            {
                "filename": "change-register.md",
                "excerpt": "The setpoint changed by 2 C.",
            },
        ],
    )

    assert unsupported == []


def test_numeric_consistency_accepts_bare_celsius_with_same_raw_time_value() -> None:
    unsupported = direction_module._unsupported_numeric_claims(
        "合同要求室外盘管至少升温 10 °C。",
        question="这次除霜符合本项目合同吗？",
        tables=[],
        citations=[
            {
                "filename": "control-sequence.md",
                "excerpt": "Raise outdoor-coil temperature by at least 10 C.",
            },
            {
                "filename": "project-overview.md",
                "excerpt": "Telemetry is sampled every 10 seconds.",
            },
        ],
    )

    assert unsupported == []


def test_numeric_consistency_rejects_same_number_with_incompatible_time_unit() -> None:
    unsupported = direction_module._unsupported_numeric_claims(
        "遥测采样间隔是 10 小时。",
        question="数据缺失时能不能直接插值？",
        tables=[],
        citations=[
            {
                "filename": "project-overview.md",
                "excerpt": "Telemetry is expected every ten seconds.",
            }
        ],
    )

    assert unsupported == [10.0]


def test_numeric_consistency_accepts_equivalent_time_unit_conversion() -> None:
    unsupported = direction_module._unsupported_numeric_claims(
        "冻结持续了 0.25 小时。",
        question="冻结持续多久？",
        tables=[],
        citations=[
            {
                "filename": "service-work-orders.md",
                "excerpt": "The frozen interval lasted fifteen minutes.",
            }
        ],
    )

    assert unsupported == []


def test_numeric_consistency_infers_hours_from_runtime_column() -> None:
    unsupported = direction_module._unsupported_numeric_claims(
        "HP-01 压缩机累计运行 1.0 小时。",
        question="比较四台机组的运行情况。",
        tables=[
            {
                "columns": ["asset_id", "compressor_runtime_h"],
                "rows": [["HP-01", 1.0]],
            }
        ],
        citations=[],
    )

    assert unsupported == []


def test_numeric_consistency_infers_event_count_from_count_column() -> None:
    unsupported = direction_module._unsupported_numeric_claims(
        "HP-01 识别到 1 次报警事件。",
        question="比较四台机组的报警。",
        tables=[
            {
                "columns": ["asset_id", "alarm_event_count"],
                "rows": [["HP-01", 1]],
            }
        ],
        citations=[],
    )

    assert unsupported == []


def test_numeric_consistency_rejects_wrong_event_count() -> None:
    unsupported = direction_module._unsupported_numeric_claims(
        "识别到 1 次报警事件。",
        question="比较四台机组的报警。",
        tables=[
            {
                "columns": ["机组", "报警事件数"],
                "rows": [["HP-01", 2]],
            }
        ],
        citations=[],
    )

    assert unsupported == [1.0]


@pytest.mark.parametrize(
    "pressure_column",
    ["min_suction_kpa_g", "min_suction_pressure"],
)
def test_numeric_consistency_infers_pressure_unit_from_table_column(
    pressure_column: str,
) -> None:
    unsupported = direction_module._unsupported_numeric_claims(
        "最低吸气压力是 320 kPa。",
        question="HP-04 是否已经确认缺冷媒？",
        tables=[
            {
                "columns": ["asset_id", pressure_column],
                "rows": [["HP-04", 320.0]],
            }
        ],
        citations=[
            {
                "filename": "service-work-orders.md",
                "excerpt": "An unrelated observation lasted 320 seconds.",
            }
        ],
    )

    assert unsupported == []


def test_numeric_consistency_accepts_localized_derived_change_measurements() -> None:
    unsupported = direction_module._unsupported_numeric_claims(
        "变更后平均送风温度下降 1.9 °C，两个小时电耗增加 4 kWh。",
        question="HP-02 为什么改设定，效果是什么？",
        tables=[
            {
                "columns": [
                    "比较窗口",
                    "平均送风温度（°C）",
                    "电耗（kWh）",
                    "压缩机偏差（Hz）",
                ],
                "rows": [
                    ["变更前", 12.2, 36.0, 1.9],
                    ["变更后", 10.3, 40.0, 1.9],
                ],
            }
        ],
        citations=[],
    )

    assert unsupported == []


def test_numeric_consistency_accepts_relative_change_percentage() -> None:
    unsupported = direction_module._unsupported_numeric_claims(
        "电耗从 36 kWh 增至 40 kWh，增幅约 11.1%。",
        question="HP-02 改设定前后效果如何？",
        tables=[
            {
                "columns": ["比较窗口", "电耗（kWh）"],
                "rows": [
                    ["变更前", 36.0],
                    ["变更后", 40.0],
                ],
            }
        ],
        citations=[],
    )

    assert unsupported == []


def test_numeric_consistency_accepts_natural_language_magnitude_of_signed_change() -> (
    None
):
    unsupported = direction_module._unsupported_numeric_claims(
        "平均 COP 相对下降 5%。",
        question="HP-02 改设定前后效果如何？",
        tables=[
            {
                "columns": ["COP 相对变化（%）"],
                "rows": [[-5.0]],
            }
        ],
        citations=[
            {
                "filename": "current-unit-configuration.md",
                "excerpt": "A command-feedback difference greater than 5 Hz is an event.",
            }
        ],
    )

    assert unsupported == []


def test_numeric_consistency_rejects_opposite_direction_for_signed_change() -> None:
    unsupported = direction_module._unsupported_numeric_claims(
        "平均 COP 相对增加 5%。",
        question="HP-02 改设定前后效果如何？",
        tables=[
            {
                "columns": ["COP 相对变化（%）"],
                "rows": [[-5.0]],
            }
        ],
        citations=[],
    )

    assert unsupported == [5.0]


def test_numeric_consistency_rejects_directional_change_supported_only_by_level() -> (
    None
):
    unsupported = direction_module._unsupported_numeric_claims(
        "数据完整率增加 50%。",
        question="各机组数据完整率是多少？",
        tables=[
            {
                "columns": ["数据完整率（%）"],
                "rows": [[50.0]],
            }
        ],
        citations=[],
    )

    assert unsupported == [50.0]


def test_measurement_direction_does_not_bleed_across_clause_boundaries() -> None:
    measurements = direction_module._measurements_in_text(
        "设定由 12°C 调至 10°C；送风均值降低 1.9°C，两小时电耗增加 4 kWh。"
    )

    assert [(item.raw_value, item.direction) for item in measurements] == [
        (12.0, None),
        (10.0, None),
        (1.9, -1),
        (2.0, None),
        (4.0, 1),
    ]


def test_numeric_consistency_uses_explicit_signed_completeness_gap() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot("data_quality")
    table = result["tables"][0]

    assert "相对100%差值（百分点）" in table["columns"]
    supported = direction_module._unsupported_numeric_claims(
        "HP-02 的完整率比其他机组低 0.231481 个百分点。",
        question="各机组数据完整率是多少？",
        tables=[table],
        citations=result["citations"],
    )
    unsupported = direction_module._unsupported_numeric_claims(
        "HP-02 的完整率比其他机组高 0.231481 个百分点。",
        question="各机组数据完整率是多少？",
        tables=[table],
        citations=result["citations"],
    )
    unsupported_percent = direction_module._unsupported_numeric_claims(
        "HP-02 的完整率比其他机组高 0.231481%。",
        question="各机组数据完整率是多少？",
        tables=[table],
        citations=result["citations"],
    )

    assert supported == []
    assert unsupported == [0.231481]
    assert unsupported_percent == [0.231481]


def test_measurement_parser_does_not_double_count_percentage_points() -> None:
    measurements = direction_module._measurements_in_text(
        "30 个百分点、30 个报警、30 个样本"
    )

    assert [(item.raw_value, item.unit_key) for item in measurements] == [
        (30.0, "percentage_points"),
        (30.0, "count"),
        (30.0, "sample_count"),
    ]


def test_data_quality_table_exposes_typed_missing_duration_inputs() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).inspect_snapshot("data_quality")
    table = result["tables"][0]

    assert "采样间隔（秒）" in table["columns"]
    assert "快照时长（秒）" in table["columns"]
    assert "缺失时长（秒）" in table["columns"]
    unsupported = direction_module._unsupported_numeric_claims(
        "快照覆盖 3 天，每 10 秒采样；HP-02 缺失 60 个点，"
        "对应 600 秒，也就是 10 分钟。",
        question="哪台机组缺数据，缺多久？",
        tables=[table],
        citations=result["citations"],
    )

    assert unsupported == []


def test_numeric_consistency_keeps_sample_counts_distinct_from_seconds() -> None:
    table = {
        "columns": ["missing_samples", "missing_duration_seconds"],
        "rows": [[60, 600]],
    }
    common = {
        "question": "How much data is missing?",
        "tables": [table],
        "citations": [],
    }

    assert (
        direction_module._unsupported_numeric_claims(
            "60 samples are missing.", **common
        )
        == []
    )
    assert direction_module._unsupported_numeric_claims(
        "600 samples are missing.", **common
    ) == [600.0]
    assert (
        direction_module._unsupported_numeric_claims(
            "The missing duration is 600 seconds.", **common
        )
        == []
    )
    assert direction_module._unsupported_numeric_claims(
        "The missing duration is 60 seconds.", **common
    ) == [60.0]


def test_exact_filename_question_collapses_citations_to_named_original_file() -> None:
    citations = [
        {
            "filename": "AGENTIC_RAG_TASK_LEDGER.json",
            "excerpt": "short chunk",
            "location": "section 1",
            "support_share_pct": 25,
        },
        {
            "filename": "AGENTIC_RAG_TASK_LEDGER.json",
            "excerpt": "full exact-file evidence for AR-C015",
            "location": "docs/AGENTIC_RAG_TASK_LEDGER.json",
            "support_share_pct": 25,
        },
        {
            "filename": "control-sequence.md",
            "excerpt": "unrelated HVAC evidence",
            "location": "docs/control-sequence.md",
            "support_share_pct": 50,
        },
    ]

    focused = DirectionAgent._focus_named_file_citations(
        "AGENTIC_RAG_TASK_LEDGER.json says what?", citations
    )

    assert focused == [
        {
            "filename": "AGENTIC_RAG_TASK_LEDGER.json",
            "excerpt": "full exact-file evidence for AR-C015",
            "location": "docs/AGENTIC_RAG_TASK_LEDGER.json",
            "support_share_pct": 100,
        }
    ]


def test_exact_filename_question_uses_one_bounded_synthesis_without_agent_loop() -> (
    None
):
    class ExactToolbox:
        def search_knowledge(self, query: str) -> dict[str, object]:
            assert "AGENTIC_RAG_TASK_LEDGER.json" in query
            return {
                "citations": [
                    {
                        "filename": "AGENTIC_RAG_TASK_LEDGER.json",
                        "excerpt": "Upload in Chat and preserve the original filename.",
                        "location": "docs/AGENTIC_RAG_TASK_LEDGER.json",
                        "source_status": "indexed",
                        "source_role": "background",
                        "support_weight": 3.0,
                    }
                ]
            }

    class ExactGenerator:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            del messages, kwargs
            self.calls += 1
            return {
                "replies": [
                    ChatMessage.from_assistant(
                        text="文件在聊天中上传，引用显示原始文件名。"
                    )
                ]
            }

    generator = ExactGenerator()
    result = DirectionAgent(ExactToolbox(), generator).answer(  # type: ignore[arg-type]
        "AGENTIC_RAG_TASK_LEDGER.json 规定了什么？"
    )

    assert generator.calls == 1
    assert result["grounding_status"] == "grounded"
    assert result["citations"] == [
        {
            "filename": "AGENTIC_RAG_TASK_LEDGER.json",
            "excerpt": "Upload in Chat and preserve the original filename.",
            "location": "docs/AGENTIC_RAG_TASK_LEDGER.json",
            "source_status": "indexed",
            "source_role": "background",
            "support_share_pct": 100,
        }
    ]
    assert result["activities"] == [
        {
            "tool": "search_project_knowledge",
            "status": "completed",
            "summary": "已读取指定文件",
        }
    ]


def test_numeric_consistency_rejects_unit_claim_supported_only_by_untyped_count() -> (
    None
):
    unsupported = direction_module._unsupported_numeric_claims(
        "该事件持续 20 小时。",
        question="事件持续多久？",
        tables=[
            {
                "columns": ["样本数"],
                "rows": [[20]],
            }
        ],
        citations=[],
    )

    assert unsupported == [20.0]


def test_numeric_consistency_prefers_matching_localized_unit_over_raw_conflict() -> (
    None
):
    unsupported = direction_module._unsupported_numeric_claims(
        "除霜期间室外盘管升温 10 °C。",
        question="如何把除霜合同表和项目数据对齐？",
        tables=[
            {
                "columns": ["室外盘管升温（°C）", "压缩机最低反馈（Hz）"],
                "rows": [[10.0, 10.0]],
            }
        ],
        citations=[],
    )

    assert unsupported == []


def test_alarm_knowledge_search_includes_sop_and_work_order() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).search_knowledge(
        "high discharge alarm air-side blockage root cause"
    )

    filenames = {citation["filename"] for citation in result["citations"]}
    assert "data-analysis-sop.md" in filenames
    assert "service-work-orders.md" in filenames
    assert "current-unit-configuration.md" in filenames


def test_project_metadata_search_exposes_numeric_sample_interval() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).search_knowledge(
        "project timezone sample interval SOP"
    )

    citations = {citation["filename"]: citation for citation in result["citations"]}
    assert "project-overview.md" in citations
    assert "manifest.json" in citations
    assert "data-analysis-sop.md" in citations
    assert '"timezone": "Asia/Shanghai"' in citations["manifest.json"]["excerpt"]
    assert '"sample_interval_seconds": 10' in citations["manifest.json"]["excerpt"]
    assert "Import metadata:" not in citations["project-overview.md"]["excerpt"]


def test_project_overview_citation_supports_numeric_sample_interval_claim() -> None:
    search = DirectionToolbox(BAKEOFF_ROOT).search_knowledge(
        "interpolate across operating-state changes sample interval"
    )

    unsupported = direction_module._unsupported_numeric_claims(
        "遥测采样间隔是 10 秒。",
        question="是否可以跨运行状态插值？",
        tables=[],
        citations=search["citations"],
    )

    assert unsupported == []


def test_hp02_configuration_comparison_search_includes_controls_review() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).search_knowledge(
        "HP-02 approved supply air setpoint before after change"
    )

    assert "controls-review.md" in {
        citation["filename"] for citation in result["citations"]
    }


def test_direction_agent_fails_closed_when_metadata_query_is_rejected() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        RejectedConfigurationThenKnowledgeGenerator(),
    ).answer("HP-02 当前批准的送风设定是多少？")

    assert result["refused"] is True
    assert result["mode"] == "safety"
    assert result["grounding_status"] == "failed"
    assert [activity["status"] for activity in result["activities"]] == [
        "completed",
        "rejected",
    ]


def test_direction_agent_fails_closed_after_rejected_runtime_query_even_with_typed_evidence() -> (
    None
):
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        RedundantQueryAfterTypedEvidenceGenerator(),
    ).answer("这次除霜符合本项目合同吗？")

    assert result["refused"] is True
    assert result["mode"] == "safety"
    assert result["grounding_status"] == "failed"
    assert [activity["status"] for activity in result["activities"]] == [
        "completed",
        "completed",
        "rejected",
    ]


def test_direction_agent_accepts_typed_fallback_after_rejected_query() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        RejectedQueryThenTypedFallbackGenerator(),
    ).answer("HP-04 最大过热度窗口在哪里？")

    assert result["grounding_status"] == "grounded"
    assert [activity["status"] for activity in result["activities"]] == [
        "rejected",
        "completed",
    ]


def test_direction_agent_accepts_refrigerant_metric_after_rejected_query() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        RejectedQueryThenRefrigerantMetricGenerator(),
    ).answer("HP-04 是不是缺冷媒？")

    assert result["grounding_status"] == "grounded"
    assert result["refused"] is False
    assert [activity["status"] for activity in result["activities"]] == [
        "completed",
        "rejected",
        "completed",
    ]
    assert {citation["filename"] for citation in result["citations"]} >= {
        "service-work-orders.md",
        "telemetry.csv",
    }


def test_direction_agent_rejects_unrelated_typed_tool_after_rejected_query() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        RejectedQueryThenUnrelatedTypedToolGenerator(),
    ).answer("Where is the maximum superheat window for HP-04?")

    assert result["refused"] is True
    assert result["grounding_status"] == "failed"


def test_direction_agent_backfills_missing_document_evidence_after_data_tool() -> None:
    generator = DataQualityImpactGenerator()
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        generator,
    ).answer("哪些数据质量问题会影响效率比较？")

    assert result["grounding_status"] == "grounded"
    assert {activity["tool"] for activity in result["activities"]} == {
        "agent",
        "inspect_hvac_snapshot",
        "search_project_knowledge",
    }
    assert "data-analysis-sop.md" in {
        citation["filename"] for citation in result["citations"]
    }
    assert generator.synthesis_requests == 1
    assert "FINAL_SYNTHESIS" in result["answer_markdown"]
    assert "UNSUPPORTED_DRAFT" not in result["answer_markdown"]


def test_direction_agent_backfills_interpolation_policy_after_current_missing_data() -> (
    None
):
    generator = DataQualityImpactGenerator()
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        generator,
    ).answer("当前导入数据有哪些缺失行，这些缺口能不能插值？")

    assert result["grounding_status"] == "grounded"
    assert generator.synthesis_requests == 1
    assert [activity["tool"] for activity in result["activities"]] == [
        "inspect_hvac_snapshot",
        "search_project_knowledge",
        "agent",
    ]
    assert {citation["filename"] for citation in result["citations"]} >= {
        "data-analysis-sop.md",
        "telemetry.csv",
    }


def test_direction_agent_backfills_data_after_document_only_quality_answer() -> None:
    generator = KnowledgeOnlyDataQualityImpactGenerator()
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        generator,
    ).answer("哪些数据质量问题会影响效率比较？")

    assert result["grounding_status"] == "grounded"
    assert generator.synthesis_requests == 1
    assert "FINAL_DATA_SYNTHESIS" in result["answer_markdown"]
    assert "DOCUMENT_ONLY_DRAFT" not in result["answer_markdown"]
    assert [activity["tool"] for activity in result["activities"]] == [
        "search_project_knowledge",
        "inspect_hvac_snapshot",
        "agent",
    ]
    assert {citation["filename"] for citation in result["citations"]} >= {
        "data-analysis-sop.md",
        "telemetry.csv",
    }


def test_direction_agent_backfills_short_cycling_contract_after_data() -> None:
    generator = ShortCyclingDataOnlyGenerator()
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        generator,
    ).answer("停机是不是太频繁？")

    assert result["grounding_status"] == "grounded"
    assert generator.synthesis_requests == 1
    assert [activity["tool"] for activity in result["activities"]] == [
        "inspect_hvac_snapshot",
        "search_project_knowledge",
        "agent",
    ]
    assert {citation["filename"] for citation in result["citations"]} >= {
        "current-unit-configuration.md",
        "telemetry.csv",
    }


def test_direction_agent_backfills_documents_after_configuration_history() -> None:
    generator = ConfigurationHistoryBackfillGenerator()
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        generator,
    ).answer("HP-02 当前批准的送风设定是多少？")

    assert result["grounding_status"] == "grounded"
    assert generator.synthesis_requests == 1
    assert "FINAL_CONFIG_SYNTHESIS" in result["answer_markdown"]
    assert "UNSUPPORTED_CONFIG_DRAFT" not in result["answer_markdown"]
    assert "search_project_knowledge" in {
        activity["tool"] for activity in result["activities"]
    }


def test_configuration_explanation_counts_as_engineering_reason() -> None:
    assert DirectionAgent._ENGINEERING_REASON_PATTERN.search("旧配置能解释当前表现吗？")


def test_project_metadata_question_rejects_redundant_data_tools() -> None:
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        ProjectMetadataOverreachGenerator(),
    ).answer("数据的时区和采样间隔是什么？")

    assert result["grounding_status"] == "grounded"
    assert [activity["status"] for activity in result["activities"]] == [
        "completed",
        "rejected",
        "rejected",
    ]
    assert {citation["filename"] for citation in result["citations"]} >= {
        "project-overview.md",
        "data-analysis-sop.md",
    }


def test_direction_agent_preserves_repeated_search_results_for_model_synthesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toolbox = DirectionToolbox(BAKEOFF_ROOT)
    search_count = 0

    def search_with_new_excerpt(query: str) -> dict[str, Any]:
        nonlocal search_count
        del query
        search_count += 1
        return {
            "summary": f"evidence passage {search_count}",
            "citations": [
                {
                    "filename": "service-work-orders.md",
                    "location": "service/service-work-orders.md",
                    "excerpt": f"unique passage {search_count}",
                    "support_weight": 1.0,
                }
            ],
        }

    monkeypatch.setattr(toolbox, "search_knowledge", search_with_new_excerpt)
    result = DirectionAgent(toolbox, RepeatedKnowledgeGenerator()).answer(
        "HP-04 的已知问题是什么？"
    )

    assert result["refused"] is False
    assert result["mode"] == "knowledge"
    assert result["grounding_status"] == "grounded"
    assert [activity["status"] for activity in result["activities"]] == [
        "completed",
        "completed",
        "completed",
    ]
    assert result["citations"][0]["excerpt"] == (
        "unique passage 1\n\nunique passage 2\n\nunique passage 3"
    )


def test_direction_agent_default_budget_allows_final_synthesis_after_full_tool_budget() -> (
    None
):
    result = DirectionAgent(
        DirectionToolbox(BAKEOFF_ROOT),
        EightEvidenceRoundsThenFinalGenerator(),
    ).answer("汇总这个项目的资料。")

    assert result["refused"] is False
    assert result["grounding_status"] == "grounded"
    assert len(result["activities"]) == 8


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


def test_direction_toolbox_auto_charts_before_after_table_when_model_omits_chart() -> (
    None
):
    result = DirectionToolbox(BAKEOFF_ROOT).query_database(
        sql="""
            SELECT
                CASE
                    WHEN timestamp < TIMESTAMPTZ '2026-01-16 12:00:00+08:00'
                    THEN '变更前'
                    ELSE '变更后'
                END AS period,
                avg(supply_air_temp_c) AS avg_supply_temp_c,
                sum(electric_power_kw * 10 / 3600) AS energy_kwh
            FROM telemetry_clean
            WHERE asset_id = 'HP-02'
              AND timestamp >= TIMESTAMPTZ '2026-01-16 10:00:00+08:00'
              AND timestamp < TIMESTAMPTZ '2026-01-16 14:00:00+08:00'
            GROUP BY period
        """,
        title="HP-02 变更前后效果",
        chart_kind="none",
        x_column="",
        y_column="",
    )

    assert result["charts"] == [
        {
            "kind": "bar",
            "title": "HP-02 变更前后效果",
            "unit": "kWh",
            "points": [
                {"label": "变更前", "value": 36.0},
                {"label": "变更后", "value": 40.0},
            ],
        }
    ]


def test_direction_toolbox_infers_chart_unit_from_temperature_title() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).query_database(
        sql="""
            SELECT
                CAST(date_trunc('minute', timestamp) AS VARCHAR) AS period,
                max(discharge_temp_c) AS metric
            FROM telemetry_clean
            WHERE asset_id = 'HP-03'
            GROUP BY period
            ORDER BY period
            LIMIT 2
        """,
        title="HP-03 排气温度趋势",
        chart_kind="line",
        x_column="period",
        y_column="metric",
    )

    assert result["charts"][0]["unit"] == "°C"


def test_direction_trend_table_labels_and_grounds_discharge_temperature() -> None:
    result = DirectionToolbox(BAKEOFF_ROOT).query_database(
        sql="""
            SELECT
                CAST(date_trunc('minute', timestamp) AS VARCHAR) AS period,
                max(discharge_temp_c) AS discharge_temp_c
            FROM telemetry_clean
            WHERE asset_id = 'HP-03'
              AND timestamp >= TIMESTAMPTZ '2026-01-15 19:59:00+08:00'
              AND timestamp < TIMESTAMPTZ '2026-01-15 20:01:00+08:00'
            GROUP BY period
            ORDER BY period
        """,
        title="HP-03 排气温度异常窗口趋势",
        chart_kind="line",
        x_column="period",
        y_column="discharge_temp_c",
    )

    table = result["tables"][0]
    assert table["columns"] == ["比较窗口", "排气温度（°C）"]
    unsupported = direction_module._unsupported_numeric_claims(
        "排气温度由 79.0902°C 提高 50.9098°C 至 130°C。",
        question="画出 HP-03 排气温度异常趋势。",
        tables=[table],
        citations=result["citations"],
    )
    assert unsupported == []


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


def test_direction_agent_default_budget_supports_one_bounded_complex_audit() -> None:
    agent = DirectionAgent(DirectionToolbox(BAKEOFF_ROOT), HistoryCaptureGenerator())

    assert agent.budget == AgentBudget(
        max_steps=11,
        max_tools=10,
        timeout_seconds=180.0,
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

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from threading import Lock
from time import monotonic
from typing import Any

from haystack import component
from haystack.components.agents import Agent
from haystack.core.errors import PipelineRuntimeError
from haystack.dataclasses import ChatMessage, ChatRole, ToolCall
from haystack.tools import Tool

from project_copilot.ingestion import ProjectIndexer, SourceCitation
from project_copilot.semantic_analytics import GovernedAnalyticsTool


class AgentBudgetError(RuntimeError):
    """Raised when a bounded Agent exceeds its tool or wall-time budget."""


@dataclass(frozen=True)
class AgentBudget:
    max_steps: int = 6
    max_tools: int = 5
    timeout_seconds: float = 15.0


@dataclass(frozen=True)
class ToolActivity:
    tool: str
    status: str
    summary: str


@dataclass(frozen=True)
class AgentAnswer:
    answer: str
    citations: tuple[SourceCitation, ...]
    activities: tuple[ToolActivity, ...]
    refused: bool
    clarification: bool
    diagnostic: dict[str, Any] | None = None


@component
class DeterministicChatGenerator:
    """Offline OpenAI-compatible test double that still uses Haystack tools."""

    @component.output_types(replies=list[ChatMessage])
    def run(
        self,
        messages: list[ChatMessage],
        tools: Any = None,
        **_: Any,
    ) -> dict[str, list[ChatMessage]]:
        del tools
        tool_results = [
            result
            for message in messages
            for result in (message.tool_call_results or [])
        ]
        if tool_results:
            summaries: list[str] = []
            for tool_result in tool_results:
                try:
                    payload = json.loads(tool_result.result)
                    summaries.append(
                        str(
                            payload.get("summary")
                            or payload.get("answer")
                            or payload.get("preview")
                            or tool_result.result
                        )
                    )
                except (json.JSONDecodeError, AttributeError):
                    summaries.append(str(tool_result.result))
            return {"replies": [ChatMessage.from_assistant(text=" ".join(summaries))]}

        question = next(
            (
                message.text
                for message in reversed(messages)
                if message.is_from(ChatRole.USER) and message.text
            ),
            "",
        )
        normalized = question.casefold().strip()
        if normalized in {"how is it?", "how is it", "怎么样", "what happened?"}:
            calls = [
                ToolCall(
                    tool_name="clarification",
                    arguments={"topic": "project aspect and time range"},
                    id="det-clarify",
                )
            ]
        else:
            calls = []
            if any(token in normalized for token in ("defrost", "除霜")):
                asset_match = re.search(r"\b[A-Z]{2,5}-\d+\b", question, re.I)
                timestamps = re.findall(
                    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?", question
                )
                if asset_match and len(timestamps) >= 2:
                    calls.extend(
                        [
                            ToolCall(
                                tool_name="configuration_lookup",
                                arguments={
                                    "query": (
                                        f"{asset_match.group(0).upper()} defrost entry "
                                        "exit outdoor fan reversing valve 300-second "
                                        "maximum SYN-HP01-DEFROST"
                                    )
                                },
                                id="det-defrost-rules",
                            ),
                            ToolCall(
                                tool_name="defrost_diagnostics",
                                arguments={
                                    "asset_id": asset_match.group(0).upper(),
                                    "start": timestamps[0],
                                    "end": timestamps[1],
                                },
                                id="det-defrost-data",
                            ),
                        ]
                    )
                else:
                    calls.append(
                        ToolCall(
                            tool_name="clarification",
                            arguments={
                                "topic": "asset ID and ISO start/end timestamps"
                            },
                            id="det-defrost-clarify",
                        )
                    )
            if any(
                token in normalized
                for token in ("meeting", "decide", "decision", "会议", "决策")
            ):
                calls.append(
                    ToolCall(
                        tool_name="meeting_decision_lookup",
                        arguments={"query": question},
                        id="det-meeting",
                    )
                )
            cop_requested = bool(re.search(r"\bcop\b", normalized))
            if cop_requested or any(
                token in normalized
                for token in (
                    "peak load",
                    "latest reading",
                    "efficiency",
                    "power",
                    "delta t",
                    "峰值",
                    "负荷",
                    "最新数据",
                    "能效",
                    "功率",
                    "温差",
                )
            ):
                if any(token in normalized for token in ("peak", "峰值", "最高")):
                    operation = "peak_load"
                elif any(
                    token in normalized for token in ("cop", "efficiency", "能效")
                ):
                    operation = "efficiency_summary"
                elif any(token in normalized for token in ("power", "功率")):
                    operation = "power_summary"
                elif any(token in normalized for token in ("delta t", "温差")):
                    operation = "temperature_delta_summary"
                else:
                    operation = "latest_reading"
                calls.append(
                    ToolCall(
                        tool_name="governed_analytics",
                        arguments={"operation": operation},
                        id="det-analytics",
                    )
                )
            if not calls and any(
                token in normalized
                for token in ("configuration", "config", "setpoint", "配置", "设定")
            ):
                calls.append(
                    ToolCall(
                        tool_name="configuration_lookup",
                        arguments={"query": question},
                        id="det-config",
                    )
                )
            if not calls:
                calls.append(
                    ToolCall(
                        tool_name="project_search",
                        arguments={"query": question},
                        id="det-search",
                    )
                )
        return {"replies": [ChatMessage.from_assistant(tool_calls=calls)]}


class ProjectAgent:
    _UNSAFE_PATTERNS = (
        re.compile(
            r"\b(run|execute|launch)\b.{0,40}\b(shell|powershell|python|cmd)\b", re.I
        ),
        re.compile(
            r"\b(change|control|start|stop|write)\b.{0,60}\b(live|equipment|chiller|pump|setpoint)\b",
            re.I,
        ),
        re.compile(r"\b(web search|browse the web|mcp)\b", re.I),
        re.compile(r"(?:执行|运行|启动).{0,30}(?:powershell|python|脚本|命令)", re.I),
        re.compile(
            r"(?:(?:远程)?(?:启动|停止|启停|控制|修改|调整|写入|设定).{0,50}"
            r"(?:空调|机组|设备|压缩机|风机|水泵|设定值|参数)|"
            r"(?:空调|机组|设备|压缩机|风机|水泵|设定值|参数).{0,50}"
            r"(?:启动|停止|启停|控制|修改|调整|写入|设定))",
            re.I,
        ),
    )

    def __init__(
        self,
        *,
        project_id: str,
        indexer: ProjectIndexer,
        analytics: GovernedAnalyticsTool,
        chat_generator: Any,
        budget: AgentBudget = AgentBudget(),
        defrost_diagnostics: Any | None = None,
    ) -> None:
        self.project_id = project_id
        self.indexer = indexer
        self.analytics = analytics
        self.chat_generator = chat_generator
        self.budget = budget
        self.defrost_diagnostics = defrost_diagnostics

    def ask(self, question: str) -> AgentAnswer:
        return asyncio.run(self.ask_async(question))

    async def ask_async(self, question: str) -> AgentAnswer:
        if any(pattern.search(question) for pattern in self._UNSAFE_PATTERNS):
            return AgentAnswer(
                answer=(
                    "Shell, unrestricted code, Web/MCP, and direct equipment control "
                    "are not available in this governed project workspace."
                ),
                citations=(),
                activities=(),
                refused=True,
                clarification=False,
            )

        started = monotonic()
        tool_count = 0
        citations: dict[tuple[str, str], SourceCitation] = {}
        activities: list[ToolActivity] = []
        refused = False
        clarification = False
        defrost_verdict: dict[str, Any] | None = None
        state_lock = Lock()

        def invoke(tool_name: str, function: Any) -> str:
            nonlocal tool_count, refused, clarification, defrost_verdict
            with state_lock:
                tool_count += 1
                activity_index = len(activities)
                activities.append(ToolActivity(tool_name, "running", "Reserved"))
                if tool_count > self.budget.max_tools:
                    activities[activity_index] = ToolActivity(
                        tool_name, "failed", "Agent tool budget exceeded"
                    )
                    raise AgentBudgetError("Agent tool budget exceeded")
                if monotonic() - started > self.budget.timeout_seconds:
                    activities[activity_index] = ToolActivity(
                        tool_name, "failed", "Agent wall-time budget exceeded"
                    )
                    raise AgentBudgetError("Agent wall-time budget exceeded")
            try:
                payload = function()
                summary = str(
                    payload.get("summary") or payload.get("answer") or "Completed"
                )
                with state_lock:
                    for citation in payload.get("citations", []):
                        item = SourceCitation(**citation)
                        citations[(item.source_id, item.excerpt)] = item
                    refused = refused or bool(payload.get("refused", False))
                    clarification = clarification or bool(
                        payload.get("clarification", False)
                    )
                    if tool_name == "defrost_diagnostics" and payload.get("status"):
                        defrost_verdict = {
                            key: payload.get(key)
                            for key in (
                                "status",
                                "asset_id",
                                "window_start",
                                "window_end",
                                "sample_count",
                                "violation_count",
                                "first_deviation_at",
                                "controller_model",
                                "firmware_version",
                                "compliance_scope",
                                "timezone",
                                "rule_id",
                                "rule_version",
                                "rule_source",
                                "rule_section",
                                "summary",
                                "timestamp_uncertainty_seconds",
                                "violations",
                                "transitions",
                                "unobservable_reasons",
                            )
                        }
                    activities[activity_index] = ToolActivity(
                        tool_name, "completed", summary[:180]
                    )
                return json.dumps(payload, ensure_ascii=False, default=str)
            except Exception as exc:
                with state_lock:
                    activities[activity_index] = ToolActivity(
                        tool_name, "failed", str(exc)[:180]
                    )
                raise

        def search_payload(
            query: str, categories: set[str] | None = None
        ) -> dict[str, Any]:
            result = self.indexer.search(
                self.project_id, query, categories=categories, top_k=5
            )
            evidence = [item.excerpt for item in result.citations]
            summary = " ".join(evidence) if evidence else result.answer
            return {
                "answer": result.answer,
                "summary": summary,
                "evidence": evidence,
                "citations": [asdict(item) for item in result.citations],
                "refused": result.refused,
            }

        def analytics_payload(operation: str) -> dict[str, Any]:
            result = self.analytics.run(operation)
            return {**asdict(result), "summary": result.summary}

        def defrost_payload(asset_id: str, start: str, end: str) -> dict[str, Any]:
            if self.defrost_diagnostics is None:
                return {
                    "summary": (
                        "No approved synthetic_demo defrost rule pack and telemetry "
                        "dataset are available in this workspace. V2 "
                        "event_reconstruction and oem_exact are disabled until an "
                        "external approval manifest and immutable evidence binding "
                        "are implemented."
                    ),
                    "refused": True,
                    "clarification": True,
                }
            result = self.defrost_diagnostics.analyze(
                asset_id=asset_id,
                start=start,
                end=end,
            )
            return {
                **asdict(result),
                "summary": result.summary,
                "refused": result.status in {"insufficient_data", "unobservable"},
                "clarification": result.status in {"insufficient_data", "unobservable"},
            }

        tools = [
            Tool(
                name="project_search",
                description="Search all imported project sources and return grounded citations.",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                function=lambda query: invoke(
                    "project_search", lambda: search_payload(query)
                ),
            ),
            Tool(
                name="configuration_lookup",
                description="Look up approved configuration and configuration-changing decisions.",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                function=lambda query: invoke(
                    "configuration_lookup",
                    lambda: search_payload(query, {"configuration", "decision"}),
                ),
            ),
            Tool(
                name="meeting_decision_lookup",
                description="Search meeting notes and decisions with citations.",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                function=lambda query: invoke(
                    "meeting_decision_lookup",
                    lambda: search_payload(query, {"meeting", "decision"}),
                ),
            ),
            Tool(
                name="governed_analytics",
                description="Run an allowlisted read-only telemetry operation.",
                parameters={
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": sorted(self.analytics.OPERATIONS),
                        }
                    },
                    "required": ["operation"],
                    "additionalProperties": False,
                },
                function=lambda operation: invoke(
                    "governed_analytics",
                    lambda: analytics_payload(operation),
                ),
            ),
            Tool(
                name="source_inspection",
                description="Inspect a specific cited source by source ID.",
                parameters={
                    "type": "object",
                    "properties": {"source_id": {"type": "string"}},
                    "required": ["source_id"],
                    "additionalProperties": False,
                },
                function=lambda source_id: invoke(
                    "source_inspection",
                    lambda: self.indexer.inspect_source(self.project_id, source_id),
                ),
            ),
            Tool(
                name="defrost_diagnostics",
                description=(
                    "Replay an approved commercial-HVAC defrost rule pack over a "
                    "bounded asset and ISO timestamp window."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "asset_id": {"type": "string"},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                    },
                    "required": ["asset_id", "start", "end"],
                    "additionalProperties": False,
                },
                function=lambda asset_id, start, end: invoke(
                    "defrost_diagnostics",
                    lambda: defrost_payload(asset_id, start, end),
                ),
            ),
            Tool(
                name="clarification",
                description="Ask the user for missing scope instead of guessing.",
                parameters={
                    "type": "object",
                    "properties": {"topic": {"type": "string"}},
                    "required": ["topic"],
                    "additionalProperties": False,
                },
                function=lambda topic: invoke(
                    "clarification",
                    lambda: {
                        "summary": f"Please specify the {topic} you want to examine.",
                        "clarification": True,
                    },
                ),
            ),
        ]
        runner = Agent(
            chat_generator=self.chat_generator,
            tools=tools,
            system_prompt=(
                "You are a governed project copilot. Use only provided tools, cite imported "
                "sources, clarify missing scope, and never request hidden chain-of-thought."
            ),
            max_agent_steps=self.budget.max_steps,
            raise_on_tool_invocation_failure=True,
        )
        try:
            result = await asyncio.wait_for(
                runner.run_async(messages=[ChatMessage.from_user(question)]),
                timeout=self.budget.timeout_seconds,
            )
        except TimeoutError:
            activities.append(
                ToolActivity("agent", "failed", "Agent wall-time budget exceeded")
            )
            return AgentAnswer(
                answer="The bounded Agent stopped: Agent wall-time budget exceeded",
                citations=tuple(citations.values()),
                activities=tuple(activities),
                refused=True,
                clarification=False,
            )
        except PipelineRuntimeError as exc:
            current: BaseException | None = exc
            while current is not None and not isinstance(current, AgentBudgetError):
                current = current.__cause__
            if isinstance(current, AgentBudgetError):
                return AgentAnswer(
                    answer=f"The bounded Agent stopped: {current}",
                    citations=tuple(citations.values()),
                    activities=tuple(activities),
                    refused=True,
                    clarification=False,
                )
            raise
        answer = result["last_message"].text or "The Agent returned no answer."
        if defrost_verdict is not None:
            verdict_label = {
                "non_compliant": "non-compliant",
                "insufficient_data": "insufficient data",
            }.get(str(defrost_verdict["status"]), str(defrost_verdict["status"]))
            deterministic_summary = str(defrost_verdict.get("summary") or "")
            explanation = (
                deterministic_summary
                if isinstance(self.chat_generator, DeterministicChatGenerator)
                else f"{deterministic_summary}\n\nAI explanation: {answer}"
            )
            answer = (
                "Deterministic defrost verdict: "
                f"{verdict_label}. "
                f"Scope: {defrost_verdict['compliance_scope']}; "
                f"asset: {defrost_verdict['asset_id']}; "
                f"controller: {defrost_verdict['controller_model']}; "
                f"firmware: {defrost_verdict['firmware_version']}; "
                f"rule: {defrost_verdict['rule_id']} "
                f"({defrost_verdict['rule_version']}, "
                f"{defrost_verdict['rule_source']} / "
                f"{defrost_verdict['rule_section']}); timestamp uncertainty: "
                f"at least +/- {defrost_verdict['timestamp_uncertainty_seconds']} "
                "seconds.\n\n"
                f"{explanation}\n\n"
                "This deterministic replay confirms only the observed rule "
                "comparison; it does not establish a physical root cause or "
                "authorize equipment operation."
            )
        return AgentAnswer(
            answer=answer,
            citations=tuple(citations.values()),
            activities=tuple(activities),
            refused=refused,
            clarification=clarification,
            diagnostic=defrost_verdict,
        )

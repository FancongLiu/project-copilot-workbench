from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any

import duckdb
from haystack.components.agents import Agent
from haystack.core.errors import PipelineRuntimeError
from haystack.dataclasses import ChatMessage
from haystack.tools import Tool

from project_copilot.agent import AgentBudget, AgentBudgetError
from project_copilot.knowledge import LocalKnowledgeIndex
from project_copilot.sql_guard import SQLPolicyError, SQLSelectGuard


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DirectionDemo:
    """Read-only product-direction demo backed by the synthetic bake-off corpus."""

    corpus_root: Path

    @property
    def database_path(self) -> Path:
        return self.corpus_root / "datasets" / "hvac_bakeoff.duckdb"

    def _citation(
        self,
        relative_path: str,
        *,
        excerpt: str,
        location: str,
        support_share_pct: int,
    ) -> dict[str, object]:
        source = self.corpus_root / "docs" / "source" / relative_path
        if not source.is_file():
            raise RuntimeError(f"Direction-demo evidence is missing: {relative_path}")
        return {
            "filename": source.name,
            "excerpt": excerpt,
            "location": location,
            "support_share_pct": support_share_pct,
        }

    @staticmethod
    def _base(*, mode: str, answer_markdown: str) -> dict[str, Any]:
        return {
            "mode": mode,
            "demo_mode": True,
            "answer_markdown": answer_markdown,
            "tables": [],
            "charts": [],
            "citations": [],
            "clarification": False,
            "refused": False,
            "grounding_status": "demo",
        }

    def answer(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return asyncio.run(self.answer_async(question, history=history))

    async def answer_async(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        normalized = "".join(question.casefold().split())
        if any(
            phrase in normalized
            for phrase in ("删除", "删掉", "改成", "写入", "下发", "控制机组")
        ):
            result = self._base(
                mode="safety",
                answer_markdown=(
                    "### 不能直接执行\n\n"
                    "这个工作台当前只做只读查询和分析，不删除原始数据，也不向机组下发控制或修改阈值。"
                    "我可以改为给出影响分析、建议变更单和需要人工确认的步骤。"
                ),
            )
            result["refused"] = True
            return result

        if "节能" in normalized and not any(
            token in normalized for token in ("2026", "1月", "小时", "天", "负荷")
        ):
            result = self._base(
                mode="clarification",
                answer_markdown=(
                    "### 还需要两个条件\n\n"
                    "请补充 **时间范围**，以及要按什么相近 **负荷** 比较。"
                    "否则机组容量、环境温度和运行工况不同，直接比较会误导。"
                ),
            )
            result["clarification"] = True
            return result

        if "原始数据" in normalized and (
            "去重" in normalized or "多少行" in normalized
        ):
            return self._row_count_answer()

        if "hp-02" in normalized and any(
            token in normalized for token in ("送风", "设定", "修改", "效果")
        ):
            return self._configuration_change_answer()

        return self._base(
            mode="clarification",
            answer_markdown=(
                "### 请把问题再具体一点\n\n"
                "这版离线方向演示只覆盖已回测的问题模板。请写明机组、时间范围和想比较的指标。"
                "接入公司批准的大模型后，同一界面会处理更自由的问法。"
            ),
        ) | {"clarification": True}

    def _row_count_answer(self) -> dict[str, Any]:
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            raw = int(
                connection.execute("SELECT count(*) FROM telemetry_raw").fetchone()[0]
            )
            unique = int(
                connection.execute("SELECT count(*) FROM telemetry_clean").fetchone()[0]
            )
            asset_count = int(
                connection.execute(
                    "SELECT count(DISTINCT asset_id) FROM telemetry_clean"
                ).fetchone()[0]
            )
        finally:
            connection.close()
        ideal = 4 * 72 * 60 * 60 // 10
        result = self._base(
            mode="data",
            answer_markdown=(
                "### 数据完整性结果\n\n"
                f"数据库重新计算得到 **{raw:,} 行原始数据**、**{unique:,} 行去重数据**。"
                f"样本覆盖 {asset_count} 台机组、72 小时、10 秒一次采样；相对理想网格少 60 个点。"
            ),
        )
        result["tables"] = [
            {
                "title": "行数核对",
                "columns": ["口径", "行数"],
                "rows": [
                    ["原始数据", f"{raw:,}"],
                    ["按机组和时间去重", f"{unique:,}"],
                    ["理想采样网格", f"{ideal:,}"],
                ],
            }
        ]
        return result

    def _configuration_change_answer(self) -> dict[str, Any]:
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            before = connection.execute(
                """
                SELECT avg(supply_air_temp_c), sum(electric_power_kw * 10 / 3600)
                FROM telemetry_clean
                WHERE asset_id = 'HP-02'
                  AND timestamp >= '2026-01-16T10:00:00+08:00'
                  AND timestamp < '2026-01-16T12:00:00+08:00'
                """
            ).fetchone()
            after = connection.execute(
                """
                SELECT avg(supply_air_temp_c), sum(electric_power_kw * 10 / 3600)
                FROM telemetry_clean
                WHERE asset_id = 'HP-02'
                  AND timestamp >= '2026-01-16T12:00:00+08:00'
                  AND timestamp < '2026-01-16T14:00:00+08:00'
                """
            ).fetchone()
            points = connection.execute(
                """
                SELECT strftime(date_trunc('minute', timestamp), '%H:%M') AS label,
                       round(avg(supply_air_temp_c), 2) AS value
                FROM telemetry_clean
                WHERE asset_id = 'HP-02'
                  AND timestamp >= '2026-01-16T10:00:00+08:00'
                  AND timestamp < '2026-01-16T14:00:00+08:00'
                  AND minute(timestamp) % 15 = 0
                  AND second(timestamp) = 0
                GROUP BY date_trunc('minute', timestamp)
                ORDER BY date_trunc('minute', timestamp)
                """
            ).fetchall()
        finally:
            connection.close()
        if before is None or after is None:
            raise RuntimeError("Direction-demo comparison query returned no result")
        before_supply, before_energy = (float(before[0]), float(before[1]))
        after_supply, after_energy = (float(after[0]), float(after[1]))
        supply_change = abs(after_supply - before_supply)
        energy_change = after_energy - before_energy
        result = self._base(
            mode="combined",
            answer_markdown=(
                "### 结论\n\n"
                "HP-02 的送风设定由 12°C 调到 10°C，会议记录给出的原因是"
                "**提高合成工艺区降温能力**。变更后的两小时里，送风温度均值降低 "
                f"**{supply_change:.1f}°C**，同期电耗增加 **{energy_change:.0f} kWh**。\n\n"
                "### 工程判断\n\n"
                "这说明本次合成测试达到了更低送风温度，但代价是电耗上升。"
                "目前只能评价这个观测窗口，不能据此认定长期能效改善。"
            ),
        )
        result["tables"] = [
            {
                "title": "变更前后两小时",
                "columns": ["比较窗口", "送风均值", "电耗"],
                "rows": [
                    [
                        "变更前 10:00–12:00",
                        f"{before_supply:.1f}°C",
                        f"{before_energy:.0f} kWh",
                    ],
                    [
                        "变更后 12:00–14:00",
                        f"{after_supply:.1f}°C",
                        f"{after_energy:.0f} kWh",
                    ],
                ],
            }
        ]
        result["charts"] = [
            {
                "kind": "line",
                "title": "HP-02 送风温度（15 分钟采样）",
                "unit": "°C",
                "points": [
                    {"label": str(label), "value": float(value)}
                    for label, value in points
                ],
            }
        ]
        result["citations"] = [
            self._citation(
                "meetings/controls-review.md",
                excerpt=(
                    "团队批准 CR-017：HP-02 在 12:00 将送风设定从 12°C 改为 10°C，"
                    "用于提高合成工艺区降温能力。"
                ),
                location="正文第 1 段",
                support_share_pct=60,
            ),
            self._citation(
                "decisions/change-register.md",
                excerpt=("CR-017 自 2026-01-16 12:00 起生效，并取代原 12°C 送风设定。"),
                location="正文第 1 段",
                support_share_pct=40,
            ),
        ]
        return result


class DirectionToolbox:
    """Bounded knowledge and read-only SQL tools over the synthetic HVAC corpus."""

    TABLES = (
        "telemetry_clean",
        "telemetry_raw",
        "config_history",
        "assets",
        "point_aliases",
    )

    _COLUMN_LABELS = {
        "asset_id": "机组",
        "model": "机型",
        "zone": "区域",
        "controller": "控制器",
        "rated_cooling_kw": "额定制冷量（kW）",
        "rated_heating_kw": "额定制热量（kW）",
        "timestamp": "时间",
        "period": "比较窗口",
        "samples": "样本数",
        "sample_count": "样本数",
        "row_count": "数据行数",
        "avg_sp_c": "平均送风设定（°C）",
        "min_sp_c": "最低送风设定（°C）",
        "max_sp_c": "最高送风设定（°C）",
        "avg_setpoint_c": "平均送风设定（°C）",
        "avg_supply_sp_c": "平均送风设定（°C）",
        "avg_supply_c": "平均送风温度（°C）",
        "avg_supply_temp_c": "平均送风温度（°C）",
        "min_supply_c": "最低送风温度（°C）",
        "min_supply_temp_c": "最低送风温度（°C）",
        "max_supply_c": "最高送风温度（°C）",
        "max_supply_temp_c": "最高送风温度（°C）",
        "avg_return_c": "平均回风温度（°C）",
        "avg_return_temp_c": "平均回风温度（°C）",
        "avg_return_air_temp_c": "平均回风温度（°C）",
        "avg_air_delta_c": "平均回送风温差（K）",
        "avg_error_c": "平均跟踪偏差（°C）",
        "avg_temp_deviation_c": "平均送风偏差（K）",
        "avg_air_temp_drop_k": "平均回送风温差（K）",
        "avg_ambient_c": "平均环境温度（°C）",
        "avg_ambient_temp_c": "平均环境温度（°C）",
        "avg_ambient_rh_pct": "平均环境湿度（%RH）",
        "avg_power_kw": "平均电功率（kW）",
        "energy_kwh": "电耗（kWh）",
        "avg_thermal_kw": "平均制冷量（kW）",
        "avg_thermal_output_kw": "平均制冷量（kW）",
        "avg_cop": "平均 COP",
        "avg_compressor_hz": "平均压缩机频率（Hz）",
        "flow_proof_samples": "流量证明样本数",
        "alarm_samples": "报警样本数",
        "max_discharge_c": "最高排气温度（°C）",
        "parameter_name": "参数名称",
        "parameter_value": "参数值",
        "unit": "单位",
        "source_file": "依据文件",
        "valid_from": "生效时间",
        "valid_to": "失效时间",
        "valid_from_text": "生效时间",
        "valid_to_text": "失效时间",
        "start_time": "开始时间",
        "end_time": "结束时间",
        "before_kwh": "调整前电耗（kWh）",
        "after_kwh": "调整后电耗（kWh）",
        "increase_kwh": "电耗变化（kWh）",
    }

    def __init__(self, corpus_root: str | Path) -> None:
        self.corpus_root = Path(corpus_root).resolve()
        self.database_path = self.corpus_root / "datasets" / "hvac_bakeoff.duckdb"
        self.knowledge = LocalKnowledgeIndex.from_directory(
            self.corpus_root / "docs" / "source"
        )
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            self.table_columns = {
                table: tuple(
                    str(row[0])
                    for row in connection.execute(f"DESCRIBE {table}").fetchall()
                )
                for table in self.TABLES
            }
        finally:
            connection.close()
        self.columns = tuple(
            sorted(
                {
                    column
                    for columns in self.table_columns.values()
                    for column in columns
                }
            )
        )
        self.guard = SQLSelectGuard(
            allowed_tables=set(self.TABLES),
            allowed_columns=None,
            max_rows=50,
        )

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, float):
            return round(value, 6)
        return value

    def search_knowledge(self, query: str) -> dict[str, Any]:
        result = self.knowledge.query(query)
        citations: list[dict[str, Any]] = []
        for citation in result.citations:
            source = citation.source.casefold()
            if "superseded" in source:
                source_status, source_role = "已废止", "历史配置"
            elif "current" in source or "change-register" in source:
                source_status, source_role = "当前有效", "现行配置/决定"
            elif "meeting" in source or "controls-review" in source:
                source_status, source_role = "已记录", "会议依据"
            else:
                source_status, source_role = "参考资料", "项目资料"
            citations.append(
                {
                    "filename": Path(citation.source).name,
                    "excerpt": citation.excerpt[:500],
                    "location": citation.source,
                    "source_status": source_status,
                    "source_role": source_role,
                    "support_weight": 1.0,
                }
            )
        return {
            "summary": (
                "\n\n".join(
                    f"[{item['filename']}] {item['excerpt']}" for item in citations
                )
                or "没有找到足够的项目文档证据。"
            ),
            "citations": citations,
            "clarification": not bool(citations),
        }

    def query_database(
        self,
        *,
        sql: str,
        title: str,
        chart_kind: str,
        x_column: str,
        y_column: str,
    ) -> dict[str, Any]:
        guarded = self.guard.validate(sql)
        table_name = guarded.tables[0]
        guarded = SQLSelectGuard(
            allowed_tables={table_name},
            allowed_columns=set(self.table_columns[table_name]),
            max_rows=50,
        ).validate(sql)
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            connection.execute("SET threads = 2")
            connection.execute("SET memory_limit = '256MB'")
            cursor = connection.execute(guarded.sql)
            columns = [item[0] for item in cursor.description]
            rows = [
                [self._json_value(value) for value in row] for row in cursor.fetchall()
            ]
        finally:
            connection.close()
        if columns and columns[0] == "period":
            rows.sort(
                key=lambda row: (
                    0 if "前" in str(row[0]) else 1 if "后" in str(row[0]) else 2,
                    str(row[0]),
                )
            )
        display_columns = [
            self._COLUMN_LABELS.get(column, column.replace("_", " "))
            for column in columns
        ]
        table = {"title": title[:100], "columns": display_columns, "rows": rows}
        charts: list[dict[str, Any]] = []
        if chart_kind in {"line", "bar"} and x_column and y_column:
            if x_column not in columns or y_column not in columns:
                raise ValueError("Chart columns must exist in the guarded query result")
            x_index = columns.index(x_column)
            y_index = columns.index(y_column)
            points = [
                {"label": str(row[x_index]), "value": float(row[y_index])}
                for row in rows
                if isinstance(row[y_index], (int, float))
            ]
            if points:
                charts.append(
                    {
                        "kind": chart_kind,
                        "title": title[:100],
                        "unit": self._unit_for(y_column),
                        "points": points,
                    }
                )
        source_filename = {
            "telemetry_clean": "telemetry.csv",
            "telemetry_raw": "telemetry.csv",
            "config_history": "config_history.csv",
            "assets": "assets.csv",
            "point_aliases": "point_aliases.csv",
        }[table_name]
        source_role = {
            "telemetry_clean": "去重运行数据",
            "telemetry_raw": "原始运行数据",
            "config_history": "配置历史",
            "assets": "资产台账",
            "point_aliases": "点位映射",
        }[table_name]
        source_citations = [
            {
                "filename": source_filename,
                "excerpt": (
                    f"合成项目的{source_role}；本次回答通过只读 DuckDB "
                    "查询重新计算，未修改源数据。"
                ),
                "location": f"datasets/{source_filename}",
                "source_status": "只读快照",
                "source_role": source_role,
                "support_weight": 2.0,
            }
        ]
        if table_name == "point_aliases":
            source_citations.append(
                {
                    "filename": "point-dictionary.csv",
                    "excerpt": (
                        "点位字典记录标准字段、单位、读写含义和控制器别名；"
                        "P_SUC 映射到 suction_pressure_kpa_g。"
                    ),
                    "location": "configuration/point-dictionary.csv",
                    "source_status": "当前有效",
                    "source_role": "点位字典",
                    "support_weight": 1.0,
                }
            )
        return {
            "summary": json.dumps(
                {"columns": columns, "rows": rows},
                ensure_ascii=False,
                default=str,
            ),
            "tables": [table],
            "charts": charts,
            "citations": source_citations,
        }

    @staticmethod
    def _unit_for(column: str) -> str:
        normalized = column.casefold()
        if normalized.endswith("_kwh") or "energy" in normalized:
            return "kWh"
        if normalized.endswith("_kw") or "power" in normalized:
            return "kW"
        if normalized.endswith("_hz"):
            return "Hz"
        if normalized.endswith("_pct") or "percent" in normalized:
            return "%"
        if normalized.endswith("_c") or "temp" in normalized:
            return "°C"
        if normalized.endswith("_k") or "delta" in normalized:
            return "K"
        return ""


class DirectionAgent:
    """Model-backed Agentic RAG facade for the direction UI."""

    _UNSAFE_PATTERNS = (
        re.compile(
            r"(?:删除|删掉|写入|下发|远程控制|启动|停止).{0,50}(?:数据|机组|设备|设定|阈值)",
            re.I,
        ),
        re.compile(
            r"(?:把|将|请|立即|帮我).{0,80}(?:改成|修改|调整|下发|启动|停止)", re.I
        ),
        re.compile(
            r"\b(?:please\s+)?(?:change|set|start|stop|control|write|delete)\b"
            r".{0,80}\b(?:equipment|unit|setpoint|threshold|data)\b",
            re.I,
        ),
        re.compile(r"\b(?:delete|update|insert|drop|attach|copy)\b", re.I),
    )

    def __init__(
        self,
        toolbox: DirectionToolbox,
        chat_generator: Any,
        budget: AgentBudget = AgentBudget(
            max_steps=6,
            max_tools=8,
            timeout_seconds=120.0,
        ),
    ) -> None:
        self.toolbox = toolbox
        self.chat_generator = chat_generator
        self.budget = budget

    @staticmethod
    def _base(answer: str) -> dict[str, Any]:
        return {
            "mode": "knowledge",
            "demo_mode": False,
            "model_backed": True,
            "answer_markdown": answer,
            "tables": [],
            "charts": [],
            "citations": [],
            "activities": [],
            "clarification": False,
            "refused": False,
            "grounding_status": "grounded",
        }

    @staticmethod
    def _normalize_citations(
        citations: dict[tuple[str, str, str], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for citation in citations.values():
            key = (str(citation["filename"]), str(citation["location"]))
            if key not in grouped:
                grouped[key] = dict(citation)
                continue
            existing = grouped[key]
            excerpt = str(citation.get("excerpt", ""))
            if excerpt and excerpt not in str(existing.get("excerpt", "")):
                existing["excerpt"] = (f"{existing.get('excerpt', '')}\n\n{excerpt}")[
                    :1_000
                ]
            existing["support_weight"] = float(
                existing.get("support_weight", 1.0)
            ) + float(citation.get("support_weight", 1.0))
        weighted = [
            (dict(item), float(item.get("support_weight", 1.0)))
            for item in grouped.values()
        ]
        if not weighted:
            return []
        total = sum(weight for _, weight in weighted)
        allocated = 0
        items: list[dict[str, Any]] = []
        for index, (item, weight) in enumerate(weighted):
            item.pop("support_weight", None)
            if index == len(weighted) - 1:
                share = 100 - allocated
            else:
                share = round(weight / total * 100)
                allocated += share
            item["support_share_pct"] = share
            items.append(item)
        return items

    def answer(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return asyncio.run(self.answer_async(question, history=history))

    async def answer_async(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        if any(pattern.search(question) for pattern in self._UNSAFE_PATTERNS):
            result = self._base(
                "### 不能直接执行\n\n"
                "这个工作台只做只读查询和分析，不删除数据、不修改阈值，"
                "也不向现场机组下发控制。"
            )
            result.update(
                {
                    "mode": "safety",
                    "model_backed": False,
                    "refused": True,
                    "grounding_status": "refused",
                }
            )
            return result

        lock = Lock()
        tables: list[dict[str, Any]] = []
        charts: list[dict[str, Any]] = []
        citations: dict[tuple[str, str, str], dict[str, Any]] = {}
        activities: list[dict[str, str]] = []
        attempted_tools: set[str] = set()
        used_tools: set[str] = set()
        clarification = False
        started = monotonic()
        tool_count = 0

        def invoke(tool_name: str, function: Any) -> str:
            nonlocal clarification, tool_count
            with lock:
                tool_count += 1
                if tool_count > self.budget.max_tools:
                    activities.append(
                        {
                            "tool": tool_name,
                            "status": "failed",
                            "summary": "Agent tool budget exceeded",
                        }
                    )
                    raise AgentBudgetError("Agent tool budget exceeded")
                if monotonic() - started > self.budget.timeout_seconds:
                    activities.append(
                        {
                            "tool": tool_name,
                            "status": "failed",
                            "summary": "Agent wall-time budget exceeded",
                        }
                    )
                    raise AgentBudgetError("Agent wall-time budget exceeded")
            payload = function()
            activity_status = str(payload.get("activity_status", "completed"))
            with lock:
                attempted_tools.add(tool_name)
                if activity_status == "completed":
                    used_tools.add(tool_name)
                tables.extend(payload.get("tables", []))
                charts.extend(payload.get("charts", []))
                for citation in payload.get("citations", []):
                    key = (
                        str(citation["filename"]),
                        str(citation["location"]),
                        str(citation.get("excerpt", "")),
                    )
                    citations[key] = dict(citation)
                clarification = clarification or bool(
                    payload.get("clarification", False)
                )
                activities.append(
                    {
                        "tool": tool_name,
                        "status": activity_status,
                        "summary": str(
                            payload.get("activity_summary")
                            or payload.get("summary", "已完成")
                        )[:180],
                    }
                )
            return json.dumps(payload, ensure_ascii=False, default=str)

        def database_payload(
            sql: str,
            title: str,
            chart_kind: str,
            x_column: str,
            y_column: str,
        ) -> dict[str, Any]:
            try:
                return self.toolbox.query_database(
                    sql=sql,
                    title=title,
                    chart_kind=chart_kind,
                    x_column=x_column,
                    y_column=y_column,
                )
            except (SQLPolicyError, duckdb.Error, ValueError) as exc:
                return {
                    "summary": (
                        "The query was rejected before execution. Correct it and retry "
                        "with one flat SELECT, approved functions and simple aliases. "
                        f"Policy reason: {type(exc).__name__}: {str(exc)[:240]}"
                    ),
                    "activity_summary": "只读查询未通过安全策略，已要求模型改写",
                    "activity_status": "rejected",
                    "retryable": True,
                }

        schema = "; ".join(
            f"{table}({', '.join(columns)})"
            for table, columns in self.toolbox.table_columns.items()
        )
        tools = [
            Tool(
                name="search_project_knowledge",
                description=(
                    "Search the imported HVAC project documents, configurations, "
                    "meetings, decisions, work orders and SOPs. Translate a Chinese "
                    "question into concise English HVAC search terms when helpful."
                ),
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                function=lambda query: invoke(
                    "search_project_knowledge",
                    lambda: self.toolbox.search_knowledge(query),
                ),
            ),
            Tool(
                name="query_hvac_database",
                description=(
                    "Run one bounded read-only DuckDB SELECT over exactly one approved table. "
                    "The SQL policy permits one flat SELECT only: no WITH/CTE, "
                    "subquery, window function, SELECT star, or file access. Use "
                    "aggregate SQL for large time ranges. Samples are 10 seconds "
                    "apart, so electrical energy kWh is "
                    "SUM(electric_power_kw * 10 / 3600). Use telemetry_raw for "
                    "raw-row counts, telemetry_clean for deduplicated analysis, "
                    "point_aliases for controller aliases, config_history for "
                    "configuration changes, and assets for equipment context. Schemas: "
                    f"{schema}. Never invent a result; use this tool for every factual "
                    "telemetry question. chart_kind is none, line, or bar. "
                    "Use simple aliases such as period or metric, not SQL reserved words."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string"},
                        "title": {"type": "string"},
                        "chart_kind": {
                            "type": "string",
                            "enum": ["none", "line", "bar"],
                        },
                        "x_column": {"type": "string"},
                        "y_column": {"type": "string"},
                    },
                    "required": [
                        "sql",
                        "title",
                        "chart_kind",
                        "x_column",
                        "y_column",
                    ],
                    "additionalProperties": False,
                },
                function=lambda sql, title, chart_kind, x_column, y_column: invoke(
                    "query_hvac_database",
                    lambda: database_payload(
                        sql, title, chart_kind, x_column, y_column
                    ),
                ),
            ),
            Tool(
                name="ask_for_clarification",
                description=(
                    "Use when asset, time range, comparison basis, or business "
                    "definition is missing and a factual answer would be misleading."
                ),
                parameters={
                    "type": "object",
                    "properties": {"missing": {"type": "string"}},
                    "required": ["missing"],
                    "additionalProperties": False,
                },
                function=lambda missing: invoke(
                    "ask_for_clarification",
                    lambda: {
                        "summary": f"请补充：{missing}",
                        "clarification": True,
                    },
                ),
            ),
        ]
        runner = Agent(
            chat_generator=self.chat_generator,
            tools=tools,
            system_prompt=(
                "你是商用空调项目助手。只依据工具返回的项目资料和数据库结果回答。"
                "先判断需要查知识、查数据还是两者都查；事实问题必须调用工具。"
                "证据不足或比较口径不明确时使用澄清工具。输出简洁中文 Markdown，"
                "如果数据库工具返回 retryable=true，必须根据策略原因改写 SQL 并重试；"
                "同一数据表尽量用一次聚合 SQL 返回所需指标，避免重复查询；"
                "正文不要重复输出 Markdown 表格，界面会自动渲染数据表；"
                "先给结论，再给依据和限制；不要显示 SQL、内部编号或思维链。"
            ),
            max_agent_steps=self.budget.max_steps,
            raise_on_tool_invocation_failure=True,
        )

        def discard_pipeline_snapshot(snapshot: Any) -> None:
            del snapshot

        try:
            messages: list[ChatMessage] = []
            for turn in (history or [])[-6:]:
                content = str(turn.get("content", ""))[:2_000].strip()
                if not content:
                    continue
                if turn.get("role") == "user":
                    messages.append(ChatMessage.from_user(content))
                elif turn.get("role") == "assistant":
                    messages.append(ChatMessage.from_assistant(text=content))
            messages.append(ChatMessage.from_user(question))
            output = await asyncio.wait_for(
                runner.run_async(
                    messages=messages,
                    snapshot_callback=discard_pipeline_snapshot,
                ),
                timeout=self.budget.timeout_seconds,
            )
            answer = (output["last_message"].text or "").strip()
        except TimeoutError:
            activities.append(
                {
                    "tool": "agent",
                    "status": "failed",
                    "summary": "Agent wall-time budget exceeded",
                }
            )
            result = self._base(
                "### 暂时无法安全完成\n\n"
                "本次分析超过了单次问答时间上限，已停止继续调用工具。"
            )
            result.update(
                {
                    "mode": "safety",
                    "refused": True,
                    "activities": activities,
                    "grounding_status": "failed",
                }
            )
            return result
        except (PipelineRuntimeError, ValueError, RuntimeError) as exc:
            logger.exception("Direction agent workflow failed closed")
            failure_types: list[str] = []
            current: BaseException | None = exc
            budget_error: AgentBudgetError | None = None
            while current is not None and len(failure_types) < 4:
                failure_types.append(type(current).__name__)
                if isinstance(current, AgentBudgetError):
                    budget_error = current
                current = current.__cause__
            if budget_error is not None and not any(
                activity["status"] == "failed" for activity in activities
            ):
                activities.append(
                    {
                        "tool": "agent",
                        "status": "failed",
                        "summary": str(budget_error),
                    }
                )
            activities.append(
                {
                    "tool": "agent",
                    "status": "failed",
                    "summary": "Model workflow failed closed: "
                    + " -> ".join(failure_types),
                }
            )
            result = self._base(
                "### 暂时无法安全完成\n\n"
                "模型生成的查询未通过只读安全检查，或工具执行失败。没有修改任何数据。"
            )
            result.update(
                {
                    "mode": "safety",
                    "refused": True,
                    "activities": activities,
                    "grounding_status": "failed",
                }
            )
            return result

        if not answer:
            activities.append(
                {
                    "tool": "agent",
                    "status": "failed",
                    "summary": "Agent stopped without a final grounded answer",
                }
            )
            result = self._base(
                "### 数据核对未完成\n\n"
                "模型调用了受控工具，但没有形成最终回答；本次中间结果未被采用。"
            )
            result.update(
                {
                    "mode": "safety",
                    "refused": True,
                    "activities": activities,
                    "grounding_status": "failed",
                }
            )
            return result

        if (
            "query_hvac_database" in attempted_tools
            and "query_hvac_database" not in used_tools
        ):
            result = self._base(
                "### 数据分析未完成\n\n"
                "数据库查询没有通过只读安全策略，且未取得可复核的数据结果。"
                "文档证据不能替代本次数据计算，请重试或缩小问题范围。"
            )
            result.update(
                {
                    "mode": "safety",
                    "refused": True,
                    "activities": activities,
                    "grounding_status": "failed",
                }
            )
            return result

        if not used_tools:
            activities.append(
                {
                    "tool": "agent",
                    "status": "failed",
                    "summary": "模型没有调用项目证据工具，回答已失败关闭",
                }
            )
            result = self._base(
                "### 无法给出有依据的回答\n\n"
                "这次模型没有引用项目证据或运行数据，因此回答已被拦截。请重试。"
            )
            result.update(
                {
                    "mode": "safety",
                    "refused": True,
                    "activities": activities,
                    "grounding_status": "failed",
                }
            )
            return result

        if (
            "search_project_knowledge" in used_tools
            and "query_hvac_database" in used_tools
        ):
            mode = "combined"
        elif "query_hvac_database" in used_tools:
            mode = "data"
        elif "ask_for_clarification" in used_tools:
            mode = "clarification"
        else:
            mode = "knowledge"
        result = self._base(answer)
        result.update(
            {
                "mode": mode,
                "tables": tables,
                "charts": charts,
                "citations": self._normalize_citations(citations),
                "activities": activities,
                "clarification": clarification,
                "grounding_status": ("clarification" if clarification else "grounded"),
            }
        )
        return result

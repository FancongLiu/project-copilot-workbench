from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import duckdb
from haystack.components.agents import Agent
from haystack.core.errors import PipelineRuntimeError
from haystack.dataclasses import ChatMessage
from haystack.tools import Tool

from project_copilot.agent import AgentBudget, AgentBudgetError
from project_copilot.hvac_snapshot import HVACSnapshotInspector
from project_copilot.knowledge import LocalKnowledgeIndex
from project_copilot.sql_guard import SQLPolicyError, SQLSelectGuard


logger = logging.getLogger(__name__)

_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    r"(?![A-Za-z0-9_])"
)
_ISO_DATE_PATTERN = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?:T|\s)")
_ISO_TIME_PATTERN = re.compile(r"(?:T|\s)(\d{2}):(\d{2})(?::(\d{2}))?")
_NUMBER_WORD_VALUES = {
    "zero": 0.0,
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
    "six": 6.0,
    "seven": 7.0,
    "eight": 8.0,
    "nine": 9.0,
    "ten": 10.0,
    "eleven": 11.0,
    "twelve": 12.0,
    "thirteen": 13.0,
    "fourteen": 14.0,
    "fifteen": 15.0,
    "sixteen": 16.0,
    "seventeen": 17.0,
    "eighteen": 18.0,
    "nineteen": 19.0,
    "twenty": 20.0,
    "零": 0.0,
    "一": 1.0,
    "二": 2.0,
    "两": 2.0,
    "三": 3.0,
    "四": 4.0,
    "五": 5.0,
    "六": 6.0,
    "七": 7.0,
    "八": 8.0,
    "九": 9.0,
    "十": 10.0,
    "十一": 11.0,
    "十二": 12.0,
    "十三": 13.0,
    "十四": 14.0,
    "十五": 15.0,
    "十六": 16.0,
    "十七": 17.0,
    "十八": 18.0,
    "十九": 19.0,
    "二十": 20.0,
}
_MEASUREMENT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?P<number>"
    r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|"
    + "|".join(_NUMBER_WORD_VALUES)
    + r")(?![A-Za-z0-9_])\s*(?P<unit>"
    r"seconds?|secs?|秒|minutes?|mins?|分钟|hours?|hrs?|小时|days?|天|"
    r"degrees?\s+c(?:elsius)?|°\s*c|℃|celsius|c|kelvin|"
    r"kwh|kw|hertz|hz|kilopascals?|kpa|percentage\s+points?|percent|%|个百分点)"
    r"(?![A-Za-z0-9_])",
    re.I,
)
_SAMPLE_MEASUREMENT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?P<number>"
    r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|"
    + "|".join(_NUMBER_WORD_VALUES)
    + r")(?![A-Za-z0-9_])\s*(?P<unit>"
    r"samples?|points?|\u4e2a?\u6837\u672c|\u4e2a?\u70b9)"
    r"(?![A-Za-z0-9_])",
    re.I,
)
_COUNT_MEASUREMENT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?P<number>"
    r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|"
    + "|".join(_NUMBER_WORD_VALUES)
    + r")(?![A-Za-z0-9_])\s*(?P<unit>"
    r"次|起|条|个(?!样本|点|百分点)|events?|alarms?|starts?)"
    r"(?![A-Za-z0-9_])",
    re.I,
)


@dataclass(frozen=True)
class _Measurement:
    raw_value: float
    canonical_value: float
    unit_key: str
    direction: int | None = None


_CHANGE_COLUMN_TOKENS = (
    "change",
    "delta",
    "difference",
    "变化",
    "差值",
    "增减",
)
_MEASUREMENT_DIRECTION_PATTERNS = (
    (
        0,
        re.compile(r"(?:幅度|绝对(?:变化|差值)?|magnitude|absolute)", re.I),
    ),
    (
        1,
        re.compile(
            r"(?:增加|上升|提高|增长|升高|上调|增幅|increase|rise|rose|higher|grew)|"
            r"(?:比|较).{0,16}(?:高|多)(?:出|于)?",
            re.I,
        ),
    ),
    (
        -1,
        re.compile(
            r"(?:减少|下降|降低|下调|降幅|decrease|drop|fell|lower|reduced)|"
            r"(?:比|较).{0,16}(?:低|少)(?:出|于)?",
            re.I,
        ),
    ),
)


def _measurement_direction(value: str, start: int, end: int) -> int | None:
    del end
    context_start = max(0, start - 28)
    preceding = value[context_start:start]
    boundary = max(
        (preceding.rfind(token) for token in "\n\r,，;；。.!！?？"),
        default=-1,
    )
    if boundary >= 0:
        context_start += boundary + 1
    context = value[context_start:start]
    closest: tuple[int, int, int] | None = None
    for priority, (direction, pattern) in enumerate(_MEASUREMENT_DIRECTION_PATTERNS):
        for match in pattern.finditer(context):
            gap = context[match.end() :]
            if not re.fullmatch(
                r"(?:\s|约|大约|近|为|达到|达|了|相对|绝对|的|by|about|approximately)*",
                gap,
                re.I,
            ):
                continue
            distance = len(gap)
            candidate = (distance, priority, direction)
            if closest is None or candidate < closest:
                closest = candidate
    return None if closest is None else closest[2]


def _is_change_column(column: str) -> bool:
    normalized = column.casefold()
    return any(token in normalized for token in _CHANGE_COLUMN_TOKENS)


def _normalize_measurement_unit(unit: str) -> tuple[str, float]:
    normalized = re.sub(r"\s+", "", unit.casefold())
    if normalized in {"second", "seconds", "sec", "secs", "秒"}:
        return "time_seconds", 1.0
    if normalized in {"minute", "minutes", "min", "mins", "分钟"}:
        return "time_seconds", 60.0
    if normalized in {"hour", "hours", "hr", "hrs", "小时"}:
        return "time_seconds", 3_600.0
    if normalized in {"day", "days", "天"}:
        return "time_seconds", 86_400.0
    if normalized in {
        "degreec",
        "degreesc",
        "degreecelsius",
        "degreescelsius",
        "°c",
        "℃",
        "celsius",
        "c",
    }:
        return "temperature_c", 1.0
    if normalized == "kelvin":
        return "temperature_k", 1.0
    if normalized in {"hertz", "hz"}:
        return "frequency_hz", 1.0
    if normalized in {"kilopascal", "kilopascals", "kpa"}:
        return "pressure_kpa", 1.0
    if normalized == "kwh":
        return "energy_kwh", 1.0
    if normalized == "kw":
        return "power_kw", 1.0
    if normalized in {"percentagepoint", "percentagepoints", "个百分点"}:
        return "percentage_points", 1.0
    if normalized in {"percent", "%"}:
        return "percent", 1.0
    if normalized in {
        "sample",
        "samples",
        "point",
        "points",
        "\u6837\u672c",
        "\u4e2a\u6837\u672c",
        "\u70b9",
        "\u4e2a\u70b9",
    }:
        return "sample_count", 1.0
    if normalized in {
        "次",
        "起",
        "条",
        "个",
        "event",
        "events",
        "alarm",
        "alarms",
        "start",
        "starts",
    }:
        return "count", 1.0
    raise ValueError(f"Unsupported measurement unit: {unit}")


def _measurements_in_text(value: str) -> list[_Measurement]:
    measurements: list[_Measurement] = []
    matches = [
        *_MEASUREMENT_PATTERN.finditer(value),
        *_SAMPLE_MEASUREMENT_PATTERN.finditer(value),
        *_COUNT_MEASUREMENT_PATTERN.finditer(value),
    ]
    for match in sorted(matches, key=lambda item: item.start()):
        number_token = match.group("number")
        raw_value = _NUMBER_WORD_VALUES.get(number_token.casefold())
        if raw_value is None:
            raw_value = float(number_token.replace(",", ""))
        unit_key, factor = _normalize_measurement_unit(match.group("unit"))
        measurements.append(
            _Measurement(
                raw_value=raw_value,
                canonical_value=raw_value * factor,
                unit_key=unit_key,
                direction=_measurement_direction(value, match.start(), match.end()),
            )
        )
    return measurements


def _measurement_from_column_value(
    column: str,
    value: object,
) -> _Measurement | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = column.casefold()
    unit_key: str | None = None
    factor = 1.0
    if "kpa" in normalized or "pressure" in normalized or "压力" in normalized:
        unit_key = "pressure_kpa"
    elif (
        "kwh" in normalized
        or "energy" in normalized
        or "电耗" in normalized
        or "能耗" in normalized
    ):
        unit_key = "energy_kwh"
    elif (
        "kw" in normalized
        or "power" in normalized
        or "功率" in normalized
        or "制冷量" in normalized
        or "制热量" in normalized
        or "能力" in normalized
    ):
        unit_key = "power_kw"
    elif "hz" in normalized or "frequency" in normalized or "频率" in normalized:
        unit_key = "frequency_hz"
    elif "seconds" in normalized or "（秒）" in column or "秒" in normalized:
        unit_key = "time_seconds"
    elif (
        normalized.endswith("_h")
        or "runtime_h" in normalized
        or "run_hours" in normalized
        or "小时" in normalized
    ):
        unit_key = "time_seconds"
        factor = 3_600.0
    elif "minutes" in normalized or "分钟" in normalized:
        unit_key = "time_seconds"
        factor = 60.0
    elif (
        normalized.endswith("_c")
        or "temp_c" in normalized
        or "temperature" in normalized
        or "°c" in normalized
        or "℃" in normalized
        or "摄氏" in normalized
    ):
        unit_key = "temperature_c"
    elif (
        normalized.endswith("_k")
        or any(
            token in normalized
            for token in ("superheat", "subcooling", "过热", "过冷", "温差")
        )
        or any(marker in normalized for marker in ("（k）", "(k)"))
    ):
        unit_key = "temperature_k"
    elif "百分点" in normalized or "percentage_point" in normalized:
        unit_key = "percentage_points"
    elif (
        "pct" in normalized
        or "%" in column
        or "percent" in normalized
        or "百分" in normalized
    ):
        unit_key = "percent"
    if (
        normalized in {"sample_count", "missing_samples", "expected_samples"}
        or normalized.endswith("_sample_count")
        or normalized.endswith("_samples")
        or "\u6837\u672c\u6570" in normalized
        or "\u7f3a\u5931\u70b9\u6570" in normalized
    ):
        unit_key = "sample_count"
    elif (
        normalized.endswith("_count")
        or normalized.endswith("事件数")
        or normalized.endswith("次数")
        or normalized.endswith("报警数")
        or normalized.endswith("启停数")
    ):
        unit_key = "count"
    if unit_key is None:
        return None
    raw_value = float(value)
    direction = None
    if _is_change_column(column):
        direction = 0 if raw_value == 0 else (1 if raw_value > 0 else -1)
    return _Measurement(
        raw_value=raw_value,
        canonical_value=raw_value * factor,
        unit_key=unit_key,
        direction=direction,
    )


class SnapshotFilterError(ValueError):
    """A caller-correctable typed snapshot filter error."""


def _connect_local_snapshot(
    database_path: Path,
    timezone_name: str | None = None,
) -> duckdb.DuckDBPyConnection:
    """Open a bounded, read-only DuckDB connection with no external access."""

    config = {
        "enable_external_access": "false",
        "autoinstall_known_extensions": "false",
        "autoload_known_extensions": "false",
        "allow_community_extensions": "false",
        "memory_limit": "256MB",
        "threads": "2",
        "max_temp_directory_size": "0GB",
    }
    connection = duckdb.connect(
        str(database_path),
        read_only=True,
        config=config,
    )
    if timezone_name:
        connection.execute("SET TimeZone = ?", [timezone_name])
    return connection


def _numbers_in_text(value: str) -> list[float]:
    numbers = [
        float(match.group(0).replace(",", ""))
        for match in _NUMBER_PATTERN.finditer(value)
    ]
    for match in _ISO_DATE_PATTERN.finditer(value):
        numbers.extend(float(component) for component in match.groups())
    for match in _ISO_TIME_PATTERN.finditer(value):
        numbers.extend(
            float(component) for component in match.groups() if component is not None
        )
    numbers.extend(
        _NUMBER_WORD_VALUES[match.group("number").casefold()]
        for match in _MEASUREMENT_PATTERN.finditer(value)
        if match.group("number").casefold() in _NUMBER_WORD_VALUES
    )
    return numbers


def _measurements_compatible(claim: _Measurement, evidence: _Measurement) -> bool:
    if claim.unit_key != evidence.unit_key:
        return False
    if claim.direction in {-1, 1} and evidence.direction != claim.direction:
        return False
    use_magnitude = claim.direction is not None or evidence.direction is not None
    claim_value = abs(claim.canonical_value) if use_magnitude else claim.canonical_value
    evidence_value = (
        abs(evidence.canonical_value) if use_magnitude else evidence.canonical_value
    )
    return abs(claim_value - evidence_value) <= max(
        0.11,
        abs(evidence_value) * 0.005,
    )


def _unsupported_numeric_claims(
    answer: str,
    *,
    question: str,
    tables: list[dict[str, Any]],
    citations: list[dict[str, Any]],
) -> list[float]:
    evidence_values = _numbers_in_text(question)
    evidence_measurements = _measurements_in_text(question)
    table_values: list[float] = []
    table_measurements_by_column: dict[tuple[int, int], list[_Measurement]] = {}
    for table_index, table in enumerate(tables):
        columns = table.get("columns", [])
        for row in table.get("rows", []):
            if not isinstance(row, list):
                continue
            for cell_index, cell in enumerate(row):
                if isinstance(cell, bool):
                    continue
                if isinstance(cell, (int, float)):
                    table_values.append(float(cell))
                elif isinstance(cell, str):
                    table_values.extend(_numbers_in_text(cell))
                column = (
                    str(columns[cell_index])
                    if isinstance(columns, list) and cell_index < len(columns)
                    else ""
                )
                column_measurement = _measurement_from_column_value(column, cell)
                if column_measurement is not None:
                    evidence_measurements.append(column_measurement)
                    table_measurements_by_column.setdefault(
                        (table_index, cell_index), []
                    ).append(column_measurement)
                evidence_measurements.extend(_measurements_in_text(f"{cell} {column}"))
    for (table_index, _), column_measurements in table_measurements_by_column.items():
        table = tables[table_index]
        table_columns = table.get("columns", [])
        table_rows = table.get("rows", [])
        first_column = (
            str(table_columns[0]).casefold()
            if isinstance(table_columns, list) and table_columns
            else ""
        )
        ordered_table = any(
            token in first_column
            for token in ("时间", "时段", "窗口", "period", "timestamp", "date")
        ) or (
            isinstance(table_rows, list)
            and len(table_rows) == 2
            and all(isinstance(row, list) and row for row in table_rows)
            and any(
                token in " ".join(str(row[0]).casefold() for row in table_rows)
                for token in ("前", "后", "before", "after")
            )
        )
        if not ordered_table:
            continue
        for index, left in enumerate(column_measurements):
            for right in column_measurements[index + 1 :]:
                if left.unit_key != right.unit_key:
                    continue
                canonical_delta = right.canonical_value - left.canonical_value
                change_direction = (
                    0 if canonical_delta == 0 else (1 if canonical_delta > 0 else -1)
                )
                evidence_measurements.append(
                    _Measurement(
                        raw_value=abs(left.raw_value - right.raw_value),
                        canonical_value=abs(canonical_delta),
                        unit_key=left.unit_key,
                        direction=change_direction,
                    )
                )
                if left.canonical_value:
                    relative_change_pct = (
                        abs(right.canonical_value - left.canonical_value)
                        / abs(left.canonical_value)
                        * 100.0
                    )
                    evidence_measurements.append(
                        _Measurement(
                            raw_value=relative_change_pct,
                            canonical_value=relative_change_pct,
                            unit_key="percent",
                            direction=change_direction,
                        )
                    )
    evidence_values.extend(table_values)
    for citation in citations:
        citation_text = " ".join(
            str(citation.get(field, ""))
            for field in ("filename", "location", "excerpt")
        )
        evidence_values.extend(_numbers_in_text(citation_text))
        evidence_measurements.extend(_measurements_in_text(citation_text))
        for labeled_value in re.finditer(
            r"[\"']?(?P<column>[A-Za-z][A-Za-z0-9_]*)[\"']?\s*:\s*"
            r"(?P<value>[-+]?\d+(?:\.\d+)?)",
            citation_text,
        ):
            labeled_measurement = _measurement_from_column_value(
                labeled_value.group("column"),
                float(labeled_value.group("value")),
            )
            if labeled_measurement is not None:
                evidence_measurements.append(labeled_measurement)

    derived_values = list(evidence_values)
    for value in table_values:
        derived_values.extend((value / 60, value / 3600, value * 10, value * 100))
    for index, left in enumerate(table_values):
        for right in table_values[index + 1 :]:
            derived_values.extend((abs(left - right), left + right))
            if right:
                derived_values.append(left / right)
            if left:
                derived_values.append(right / left)

    supported_measurement_values: list[float] = []
    unsupported_measurement_values: list[float] = []
    for claim_measurement in _measurements_in_text(answer):
        compatible = any(
            _measurements_compatible(claim_measurement, evidence)
            for evidence in evidence_measurements
        )
        if compatible:
            supported_measurement_values.append(claim_measurement.raw_value)
            continue
        unsupported_measurement_values.append(claim_measurement.raw_value)

    unsupported: list[float] = []
    for claim in _numbers_in_text(answer):
        if any(abs(claim - value) <= 1e-9 for value in unsupported_measurement_values):
            unsupported.append(claim)
            continue
        if any(abs(claim - value) <= 1e-9 for value in supported_measurement_values):
            continue
        if abs(claim) < 10:
            continue
        if any(
            abs(claim - evidence) <= max(0.11, abs(evidence) * 0.005)
            for evidence in derived_values
        ):
            continue
        unsupported.append(claim)
    return unsupported


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
        connection = _connect_local_snapshot(self.database_path)
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
        connection = _connect_local_snapshot(self.database_path)
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
        "change_time": "变更生效时间",
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
        "discharge_temp_c": "排气温度（°C）",
        "max_discharge_c": "最高排气温度（°C）",
        "parameter_name": "参数名称",
        "parameter_value": "参数值",
        "unit": "单位",
        "source_file": "依据文件",
        "change_id": "变更编号",
        "valid_from": "生效时间",
        "valid_to": "失效时间",
        "valid_from_text": "生效时间",
        "valid_to_text": "失效时间",
        "start_time": "开始时间",
        "end_time": "结束时间",
        "before_kwh": "调整前电耗（kWh）",
        "after_kwh": "调整后电耗（kWh）",
        "increase_kwh": "电耗变化（kWh）",
        "event_type": "事件类型",
        "event_count": "事件数",
        "duration_seconds": "持续时间（秒）",
        "expected_samples": "预期样本数",
        "missing_samples": "缺失样本数",
        "completeness_pct": "完整率（%）",
        "completeness_gap_pp": "相对100%差值（百分点）",
        "sample_interval_seconds": "采样间隔（秒）",
        "snapshot_duration_seconds": "快照时长（秒）",
        "missing_duration_seconds": "缺失时长（秒）",
        "start_count": "启动次数",
        "threshold_start_count": "短循环阈值（次/小时）",
        "threshold_exceedance_pct": "超过阈值（%）",
        "average_deviation": "平均命令反馈偏差",
        "max_deviation": "最大命令反馈偏差",
        "average_command": "平均命令",
        "average_feedback": "平均反馈",
        "min_command": "最低命令",
        "min_feedback": "最低反馈",
        "max_feedback": "最高反馈",
        "min_compressor_cmd_hz": "最低压缩机命令（Hz）",
        "min_compressor_fb_hz": "最低压缩机反馈（Hz）",
        "max_outdoor_fan_fb_pct": "最高室外风机反馈（%）",
        "max_discharge_temp_c": "最高排气温度（°C）",
        "outdoor_coil_temp_rise_c": "室外盘管升温（°C）",
        "average_outdoor_fan_cmd_pct": "室外风机平均命令（%）",
        "average_outdoor_fan_fb_pct": "室外风机平均反馈（%）",
        "average_compressor_cmd_hz": "压缩机平均命令（Hz）",
        "average_compressor_fb_hz": "压缩机平均反馈（Hz）",
        "metric": "指标",
        "extreme_value": "极值",
        "average_thermal_output_kw": "平均能力（kW）",
        "average_cop": "平均 COP",
        "average_superheat_k": "平均过热度（K）",
    }

    def __init__(
        self,
        corpus_root: str | Path,
        *,
        workspace_search: Callable[[str], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.corpus_root = Path(corpus_root).resolve()
        self.workspace_search = workspace_search
        self.database_path = self.corpus_root / "datasets" / "hvac_bakeoff.duckdb"
        try:
            self.manifest_path = self.corpus_root / "manifest.json"
            self.manifest_text = self.manifest_path.read_text(encoding="utf-8")
            manifest = json.loads(self.manifest_text)
            self.timezone_name = str(manifest["timezone"])
            self.timezone = ZoneInfo(self.timezone_name)
            self.sample_interval_seconds = int(manifest["sample_interval_seconds"])
            if self.sample_interval_seconds <= 0:
                raise ValueError("sample interval must be positive")
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
            ZoneInfoNotFoundError,
        ) as exc:
            raise RuntimeError("HVAC corpus timezone is unavailable") from exc
        self.snapshot_inspector = HVACSnapshotInspector(
            self.database_path,
            timezone_name=self.timezone_name,
            sample_seconds=self.sample_interval_seconds,
        )
        self.knowledge = LocalKnowledgeIndex.from_directory(
            self.corpus_root / "docs" / "source"
        )
        connection = self._connect_read_only()
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

    def _connect_read_only(self) -> duckdb.DuckDBPyConnection:
        return _connect_local_snapshot(
            self.database_path,
            self.timezone_name,
        )

    def snapshot_date_bounds(self) -> tuple[date, date]:
        connection = self._connect_read_only()
        try:
            row = connection.execute(
                "SELECT CAST(min(timestamp) AS DATE), "
                "CAST(max(timestamp) AS DATE) FROM telemetry_clean"
            ).fetchone()
        finally:
            connection.close()
        if row is None or not isinstance(row[0], date) or not isinstance(row[1], date):
            raise RuntimeError("HVAC snapshot has no usable time bounds")
        return row[0], row[1]

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, float):
            return round(value, 6)
        return value

    def search_knowledge(self, query: str) -> dict[str, Any]:
        normalized_query = query.casefold()
        metadata_query = bool(
            re.search(
                r"(?:timezone|时区).{0,30}(?:sample|采样间隔)|"
                r"(?:sample|采样间隔).{0,30}(?:timezone|时区)",
                normalized_query,
                re.I,
            )
        )
        if metadata_query:
            query = (
                f"{query} project overview data analysis SOP Asia Shanghai "
                "sample interval telemetry"
            )
        if re.search(r"(?:alarm|报警|排温|a\d{3})", normalized_query, re.I):
            query = (
                f"{query} data analysis SOP command feedback read-only work order "
                "root cause inspection"
            )
        results = [self.knowledge.query(query)]
        if re.search(r"(?:discharge|排温|排气温度)", normalized_query, re.I):
            results.append(
                self.knowledge.query(
                    "current approved high discharge temperature threshold "
                    "current-unit-configuration"
                )
            )
        if "hp-02" in normalized_query and re.search(
            r"(?:setpoint|before|after|change|参数|前后|修改|配置)",
            normalized_query,
            re.I,
        ):
            results.append(
                self.knowledge.query(
                    "HP-02 controls review CR-017 approved supply-air setpoint "
                    "change reason"
                )
            )
        citations: list[dict[str, Any]] = []
        seen_citations: set[tuple[str, str]] = set()
        for citation in (
            citation for result in results for citation in result.citations
        ):
            citation_key = (citation.source, citation.excerpt)
            if citation_key in seen_citations:
                continue
            seen_citations.add(citation_key)
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
        if self.workspace_search is not None:
            workspace_citations = self.workspace_search(query)
            clarification_messages = [
                str(citation["_clarification_message"])
                for citation in workspace_citations
                if citation.get("_clarification_message")
            ]
            if clarification_messages:
                return {
                    "summary": "\n".join(clarification_messages),
                    "citations": [],
                    "clarification": True,
                }
            for citation in workspace_citations:
                citation_key = (
                    str(citation.get("filename", "")),
                    str(citation.get("excerpt", "")),
                )
                if citation_key in seen_citations:
                    continue
                seen_citations.add(citation_key)
                citations.append(citation)
            exact_workspace_citations = [
                citation
                for citation in workspace_citations
                if citation.get("_exact_filename") is True
            ]
            if exact_workspace_citations:
                citations = exact_workspace_citations
            for citation in citations:
                citation.pop("_exact_filename", None)
        normalized_query = query.casefold()
        if any(
            term in normalized_query
            for term in ("timezone", "sample interval", "时区", "采样间隔")
        ):
            metadata_start = self.manifest_text.index('"timezone"')
            sample_line_start = self.manifest_text.index(
                '"sample_interval_seconds"', metadata_start
            )
            metadata_end = self.manifest_text.find("\n", sample_line_start)
            if metadata_end < 0:
                metadata_end = len(self.manifest_text)
            citations.append(
                {
                    "filename": "manifest.json",
                    "excerpt": self.manifest_text[metadata_start:metadata_end].strip(),
                    "location": "manifest.json",
                    "source_status": "导入快照",
                    "source_role": "导入元数据",
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
        connection = self._connect_read_only()
        try:
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
                        "unit": self._unit_for(y_column, title),
                        "points": points,
                    }
                )
        if (
            not charts
            and chart_kind == "none"
            and "period" in columns
            and len(rows) >= 2
        ):
            x_index = columns.index("period")
            for candidate in (
                "energy_kwh",
                "avg_supply_temp_c",
                "avg_supply_c",
                "avg_power_kw",
                "avg_cop",
            ):
                if candidate not in columns:
                    continue
                y_index = columns.index(candidate)
                points = [
                    {"label": str(row[x_index]), "value": float(row[y_index])}
                    for row in rows
                    if isinstance(row[y_index], (int, float))
                ]
                if len(points) == len(rows):
                    charts.append(
                        {
                            "kind": "bar",
                            "title": title[:100],
                            "unit": self._unit_for(candidate),
                            "points": points,
                        }
                    )
                    break
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

    def inspect_snapshot(
        self,
        operation: str,
        asset_id: str = "",
        event_type: str = "",
        event_types: list[str] | None = None,
        start_time: str = "",
        end_time: str = "",
        alarm_code: str = "",
    ) -> dict[str, Any]:
        result = self.snapshot_inspector.inspect(operation)
        selected_asset = asset_id.strip()
        selected_alarm = alarm_code.strip().upper()
        requested_events = {
            item.strip()
            for item in (event_types or [])
            if isinstance(item, str) and item.strip()
        }
        requested_events.update(
            item.strip() for item in re.split(r"[,|]", event_type) if item.strip()
        )
        expanded_events: set[str] = set()
        for requested_event in requested_events:
            if requested_event in {
                "frozen_sensor_tuples",
                "telemetry_freeze",
            }:
                expanded_events.add("frozen_sensor_tuple")
            elif requested_event in {
                "fan_feedback_mismatch",
                "fan_command_feedback_mismatch",
            }:
                expanded_events.update(
                    {
                        "outdoor_fan_feedback_mismatch",
                        "indoor_fan_feedback_mismatch",
                    }
                )
            elif requested_event == "outdoor_fan_command_feedback_mismatch":
                expanded_events.add("outdoor_fan_feedback_mismatch")
            elif requested_event == "indoor_fan_command_feedback_mismatch":
                expanded_events.add("indoor_fan_feedback_mismatch")
            elif requested_event in {
                "eev_command_feedback_mismatch",
                "eev_feedback_mismatch_observation",
            }:
                expanded_events.add("eev_feedback_mismatch")
            elif operation == "alarm_events" and requested_event in {
                "discharge_temperature_alarm",
                "high_discharge_temperature",
            }:
                selected_alarm = selected_alarm or "A217"
            else:
                expanded_events.add(requested_event)
        requested_events = expanded_events

        def parse_boundary(value: str) -> datetime | None:
            if not value.strip():
                return None
            try:
                parsed = datetime.fromisoformat(value.strip())
            except ValueError as exc:
                raise SnapshotFilterError(
                    "Snapshot time filters require a full ISO date and time"
                ) from exc
            return (
                parsed.replace(tzinfo=self.timezone)
                if parsed.tzinfo is None
                else parsed
            )

        selected_start = parse_boundary(start_time)
        selected_end = parse_boundary(end_time)
        if selected_start and selected_end and selected_start >= selected_end:
            raise SnapshotFilterError(
                "Snapshot filter start_time must be before end_time"
            )

        def overlaps_time_window(row: dict[str, Any]) -> bool:
            row_start_value = row.get("start_time")
            row_end_value = row.get("end_time")
            if not isinstance(row_start_value, str) or not isinstance(
                row_end_value, str
            ):
                return selected_start is None and selected_end is None
            row_start = datetime.fromisoformat(row_start_value)
            row_end = datetime.fromisoformat(row_end_value)
            return (selected_start is None or row_end > selected_start) and (
                selected_end is None or row_start < selected_end
            )

        rows = [
            dict(row)
            for row in result.rows
            if (not selected_asset or row.get("asset_id") == selected_asset)
            and (
                not selected_alarm
                or str(row.get("alarm_code", "")).upper() == selected_alarm
            )
            and (not requested_events or row.get("event_type") in requested_events)
            and overlaps_time_window(row)
        ]
        for row in rows:
            completeness = row.get("completeness_pct")
            if row.get("event_type") == "coverage" and isinstance(
                completeness, (int, float)
            ):
                row["completeness_gap_pp"] = round(float(completeness) - 100.0, 6)
                row["sample_interval_seconds"] = self.sample_interval_seconds
                missing_samples = row.get("missing_samples")
                if isinstance(missing_samples, (int, float)):
                    row["missing_duration_seconds"] = int(
                        float(missing_samples) * self.sample_interval_seconds
                    )
                row_start = row.get("start_time")
                row_end = row.get("end_time")
                if isinstance(row_start, str) and isinstance(row_end, str):
                    row["snapshot_duration_seconds"] = (
                        int(
                            (
                                datetime.fromisoformat(row_end)
                                - datetime.fromisoformat(row_start)
                            ).total_seconds()
                        )
                        + self.sample_interval_seconds
                    )
        preferred_columns = (
            "event_type",
            "asset_id",
            "alarm_code",
            "start_time",
            "end_time",
            "sample_count",
            "duration_seconds",
            "event_count",
            "start_count",
            "threshold_start_count",
            "threshold_exceedance_pct",
            "expected_samples",
            "missing_samples",
            "completeness_pct",
            "completeness_gap_pp",
            "sample_interval_seconds",
            "snapshot_duration_seconds",
            "missing_duration_seconds",
            "average_deviation",
            "max_deviation",
            "average_command",
            "average_feedback",
            "min_command",
            "min_feedback",
            "max_feedback",
            "min_compressor_cmd_hz",
            "min_compressor_fb_hz",
            "max_outdoor_fan_fb_pct",
            "outdoor_coil_temp_rise_c",
            "max_discharge_temp_c",
            "average_outdoor_fan_cmd_pct",
            "average_outdoor_fan_fb_pct",
            "average_compressor_cmd_hz",
            "average_compressor_fb_hz",
        )
        columns = [
            column for column in preferred_columns if any(column in row for row in rows)
        ]
        if not rows and operation in {"control_events", "alarm_events"}:
            columns = ["event_type", "asset_id", "start_time", "end_time"]
        title = result.title
        selected_event_label = ", ".join(sorted(requested_events))
        if selected_asset or selected_alarm or requested_events:
            event_label = (
                "除霜" if requested_events == {"defrost"} else selected_event_label
            )
            title = " ".join(
                item
                for item in (selected_asset, selected_alarm, event_label, title)
                if item
            )
        row_event_types = {
            str(row.get("event_type", "")) for row in rows if row.get("event_type")
        }

        def column_label(column: str) -> str:
            if column in {"average_deviation", "max_deviation"} and row_event_types:
                prefix = "平均" if column == "average_deviation" else "最大"
                if all(
                    event_type.startswith("compressor_")
                    for event_type in row_event_types
                ):
                    return f"{prefix}命令反馈偏差（Hz）"
                if all(
                    event_type.startswith("eev_") or "_fan_" in event_type
                    for event_type in row_event_types
                ):
                    return f"{prefix}命令反馈偏差（个百分点）"
            if (
                column
                in {
                    "average_command",
                    "average_feedback",
                    "min_command",
                    "min_feedback",
                    "max_feedback",
                }
                and row_event_types
            ):
                unit = ""
                if all(
                    event_type.startswith("compressor_")
                    for event_type in row_event_types
                ):
                    unit = "Hz"
                elif all(
                    event_type.startswith("eev_") or "_fan_" in event_type
                    for event_type in row_event_types
                ):
                    unit = "%"
                if unit:
                    prefix = {
                        "average_command": "平均命令",
                        "average_feedback": "平均反馈",
                        "min_command": "最低命令",
                        "min_feedback": "最低反馈",
                        "max_feedback": "最高反馈",
                    }[column]
                    return f"{prefix}（{unit}）"
            return self._COLUMN_LABELS.get(column, column.replace("_", " "))

        table = {
            "title": title,
            "columns": [column_label(column) for column in columns],
            "rows": [[row.get(column) for column in columns] for row in rows],
        }
        charts: list[dict[str, Any]] = []
        duration_rows = [
            row for row in rows if isinstance(row.get("duration_seconds"), (int, float))
        ]
        if operation == "control_events" and duration_rows:
            event_label = "除霜" if requested_events == {"defrost"} else "控制事件"
            charts.append(
                {
                    "kind": "bar",
                    "title": (
                        f"{selected_asset + ' ' if selected_asset else ''}"
                        f"{event_label}事件持续时间"
                    ),
                    "unit": "秒",
                    "points": [
                        {
                            "label": str(row.get("start_time", row.get("event_type"))),
                            "value": float(row["duration_seconds"]),
                        }
                        for row in duration_rows
                    ],
                }
            )
        return {
            "summary": (
                result.summary
                if rows
                else "没有找到符合筛选条件的快照事件；已保留原资产、事件和时间边界。"
            ),
            "activity_summary": (
                f"已完成{title}" if rows else "筛选范围内没有发现快照事件"
            ),
            "activity_status": "completed",
            "tables": [table],
            "charts": charts,
            "citations": [
                {
                    "filename": "telemetry.csv",
                    "excerpt": (
                        "合成项目的只读遥测快照；事件和数据质量结果由受控 "
                        "DuckDB 分析操作重新计算。"
                    ),
                    "location": "datasets/telemetry.csv",
                    "source_status": "只读快照",
                    "source_role": "运行数据与事件盘点",
                    "support_weight": 2.0,
                }
            ],
        }

    def inspect_configuration_history(
        self,
        asset_id: str,
        parameter_name: str,
    ) -> dict[str, Any]:
        selected_asset = asset_id.strip()
        selected_parameter = parameter_name.strip()
        connection = self._connect_read_only()
        try:
            cursor = connection.execute(
                """
                SELECT
                    asset_id,
                    parameter_name,
                    parameter_value,
                    unit,
                    CAST(valid_from AS VARCHAR) AS valid_from_text,
                    CAST(valid_to AS VARCHAR) AS valid_to_text,
                    change_id,
                    source_file
                FROM config_history
                WHERE (? = '' OR asset_id = ?)
                  AND (? = '' OR parameter_name = ?)
                ORDER BY valid_from, asset_id, parameter_name
                LIMIT 50
                """,
                [
                    selected_asset,
                    selected_asset,
                    selected_parameter,
                    selected_parameter,
                ],
            )
            columns = [str(item[0]) for item in cursor.description]
            rows = [
                [self._json_value(value) for value in row] for row in cursor.fetchall()
            ]
        finally:
            connection.close()
        citations = [
            {
                "filename": "config_history.csv",
                "excerpt": "合成项目中按生效时间排序的只读配置历史。",
                "location": "datasets/config_history.csv",
                "source_status": "只读快照",
                "source_role": "配置版本与生效边界",
                "support_weight": 2.0,
            }
        ]
        current_configuration = (
            self.corpus_root
            / "docs"
            / "source"
            / "configuration"
            / "current-unit-configuration.md"
        )
        if current_configuration.is_file():
            citations.append(
                {
                    "filename": current_configuration.name,
                    "excerpt": current_configuration.read_text(encoding="utf-8")[:1000],
                    "location": "configuration/current-unit-configuration.md",
                    "source_status": "当前有效",
                    "source_role": "现行配置",
                    "support_weight": 2.0,
                }
            )
        return {
            "summary": json.dumps(
                {"columns": columns, "rows": rows},
                ensure_ascii=False,
                default=str,
            ),
            "activity_summary": "已完成当前与历史配置核对",
            "tables": [
                {
                    "title": "配置历史",
                    "columns": [
                        self._COLUMN_LABELS.get(column, column.replace("_", " "))
                        for column in columns
                    ],
                    "rows": rows,
                }
            ],
            "charts": [],
            "citations": citations,
        }

    def inspect_configuration_change_effect(
        self,
        asset_id: str,
        parameter_name: str,
    ) -> dict[str, Any]:
        """Compare the two hours before and after an approved setpoint change."""

        selected_asset = asset_id.strip()
        selected_parameter = parameter_name.strip()
        if not selected_asset:
            raise ValueError("asset_id is required")
        if selected_parameter != "supply_air_sp_c":
            raise ValueError(
                "Only supply_air_sp_c is supported for configuration-effect analysis"
            )

        connection = self._connect_read_only()
        try:
            change = connection.execute(
                """
                SELECT CAST(valid_from AS VARCHAR), parameter_value
                FROM config_history
                WHERE asset_id = ? AND parameter_name = ?
                ORDER BY valid_from DESC
                LIMIT 1
                """,
                [selected_asset, selected_parameter],
            ).fetchone()
            if change is None:
                raise ValueError("No approved configuration change was found")
            change_time = datetime.fromisoformat(str(change[0]))
            source_rows = connection.execute(
                """
                SELECT source_file, valid_to IS NULL AS is_current
                FROM config_history
                WHERE asset_id = ? AND parameter_name = ?
                ORDER BY valid_from
                """,
                [selected_asset, selected_parameter],
            ).fetchall()

            expected_samples = 2 * 60 * 60 // self.sample_interval_seconds

            def summarize(start: datetime, end: datetime) -> list[Any]:
                row = connection.execute(
                    """
                    SELECT
                        count(*),
                        CAST(min(timestamp) AS VARCHAR),
                        CAST(max(timestamp) AS VARCHAR),
                        round(avg(supply_air_sp_c), 1),
                        round(avg(supply_air_temp_c), 1),
                        round(sum(electric_power_kw * ? / 3600.0), 1),
                        round(avg(thermal_output_kw), 1),
                        round(avg(cop), 2)
                    FROM telemetry_clean
                    WHERE asset_id = ?
                      AND timestamp >= ?
                      AND timestamp < ?
                    """,
                    [self.sample_interval_seconds, selected_asset, start, end],
                ).fetchone()
                if row is None or any(value is None for value in row):
                    raise ValueError(
                        "The imported snapshot does not cover both comparison windows"
                    )
                sample_count = int(row[0])
                actual_start = datetime.fromisoformat(str(row[1]))
                actual_end = datetime.fromisoformat(str(row[2]))
                expected_end = end - timedelta(seconds=self.sample_interval_seconds)
                if (
                    sample_count != expected_samples
                    or actual_start != start
                    or actual_end != expected_end
                ):
                    raise ValueError(
                        "The imported snapshot lacks complete two-hour comparison windows"
                    )
                metrics = [self._json_value(value) for value in row[3:]]
                return [
                    *metrics,
                    sample_count,
                    expected_samples,
                    100.0,
                ]

            before = summarize(change_time - timedelta(hours=2), change_time)
            after = summarize(change_time, change_time + timedelta(hours=2))
        finally:
            connection.close()

        change_time_text = self._json_value(change_time)
        columns = [
            "period",
            "change_time",
            "avg_setpoint_c",
            "avg_supply_temp_c",
            "energy_kwh",
            "avg_thermal_output_kw",
            "avg_cop",
            "sample_count",
            "expected_samples",
            "completeness_pct",
        ]
        rows = [
            ["变更前", change_time_text, *before],
            ["变更后", change_time_text, *after],
        ]

        def absolute_change(metric_index: int) -> float:
            return round(float(after[metric_index]) - float(before[metric_index]), 1)

        def relative_change_pct(metric_index: int) -> float:
            baseline = float(before[metric_index])
            if baseline == 0:
                raise ValueError(
                    "Cannot calculate relative change from a zero baseline"
                )
            return round(
                (float(after[metric_index]) - baseline) / abs(baseline) * 100.0,
                1,
            )

        delta_table = {
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
            "rows": [
                [
                    absolute_change(0),
                    absolute_change(1),
                    absolute_change(2),
                    absolute_change(3),
                    absolute_change(4),
                    relative_change_pct(2),
                    relative_change_pct(3),
                    relative_change_pct(4),
                ]
            ],
        }
        citations = [
            {
                "filename": "config_history.csv",
                "excerpt": "按生效时间核对的只读配置历史。",
                "location": "datasets/config_history.csv",
                "source_status": "只读快照",
                "source_role": "配置版本与生效边界",
                "support_weight": 2.0,
            },
            {
                "filename": "telemetry.csv",
                "excerpt": "配置生效前后各两小时的只读运行数据聚合结果。",
                "location": "datasets/telemetry.csv",
                "source_status": "只读快照",
                "source_role": "配置效果观测",
                "support_weight": 2.0,
            },
        ]
        documents_root = self.corpus_root / "docs" / "source"
        document_paths = {
            path.name: path
            for path in sorted(documents_root.rglob("*"))
            if path.is_file()
        }
        for source_file, is_current in source_rows:
            filename = str(source_file)
            source_path = document_paths.get(filename)
            if source_path is None:
                continue
            citations.append(
                {
                    "filename": filename,
                    "excerpt": source_path.read_text(encoding="utf-8")[:1000],
                    "location": source_path.relative_to(documents_root).as_posix(),
                    "source_status": "当前有效" if is_current else "已废止",
                    "source_role": "批准变更" if is_current else "历史配置",
                    "support_weight": 2.0,
                }
            )

        return {
            "summary": json.dumps(
                {"columns": columns, "rows": rows},
                ensure_ascii=False,
                default=str,
            ),
            "activity_summary": "已完成配置变更前后两小时效果核对",
            "tables": [
                {
                    "title": "配置变更前后两小时",
                    "columns": [
                        self._COLUMN_LABELS.get(column, column.replace("_", " "))
                        for column in columns
                    ],
                    "rows": rows,
                },
                delta_table,
            ],
            "charts": [
                {
                    "kind": "bar",
                    "title": "配置变更前后两小时电耗",
                    "unit": "kWh",
                    "points": [
                        {"label": str(row[0]), "value": float(row[4])} for row in rows
                    ],
                }
            ],
            "citations": citations,
        }

    def inspect_metric_extreme(
        self, metric: str, direction: str, asset_id: str
    ) -> dict[str, Any]:
        result = self.snapshot_inspector.metric_extreme(
            metric,
            direction,
            asset_id or None,
        )
        columns = [
            "event_type",
            "asset_id",
            "metric",
            "unit",
            "extreme_value",
            "start_time",
            "end_time",
            "sample_count",
            "duration_seconds",
            "average_thermal_output_kw",
            "average_cop",
            "average_superheat_k",
        ]
        extreme_unit = (
            str(result.rows[0].get("unit", "")) if result.rows else ""
        ).strip()

        def column_label(column: str) -> str:
            if column == "extreme_value" and extreme_unit:
                return f"极值（{extreme_unit}）"
            return self._COLUMN_LABELS.get(column, column.replace("_", " "))

        return {
            "summary": result.summary,
            "activity_summary": f"已完成{metric}极值窗口检查",
            "tables": [
                {
                    "title": result.title,
                    "columns": [column_label(column) for column in columns],
                    "rows": [
                        [row.get(column) for column in columns] for row in result.rows
                    ],
                }
            ],
            "charts": [],
            "citations": [
                {
                    "filename": "telemetry.csv",
                    "excerpt": (
                        "合成项目的只读遥测快照；极值窗口和关联性能指标由受控 "
                        "DuckDB 操作重新计算。"
                    ),
                    "location": "datasets/telemetry.csv",
                    "source_status": "只读快照",
                    "source_role": "指标极值窗口",
                    "support_weight": 2.0,
                }
            ],
        }

    @staticmethod
    def _unit_for(column: str, context: str = "") -> str:
        normalized = column.casefold()
        context_normalized = context.casefold()
        if normalized.endswith("_kwh") or "energy" in normalized:
            return "kWh"
        if normalized.endswith("_kw") or "power" in normalized:
            return "kW"
        if normalized.endswith("_hz"):
            return "Hz"
        if normalized.endswith("_pct") or "percent" in normalized:
            return "%"
        if (
            normalized.endswith("_c")
            or "temp" in normalized
            or "温度" in context_normalized
            or "排温" in context_normalized
        ):
            return "°C"
        if normalized.endswith("_k") or "delta" in normalized:
            return "K"
        return ""


class DirectionAgent:
    """Model-backed Agentic RAG facade for the direction UI."""

    _UNSAFE_PATTERNS = (
        re.compile(
            r"(?:删除|删掉|清除|写入|下发|远程控制|启动|停止).{0,50}"
            r"(?:数据|记录|行|异常|机组|设备|设定|阈值)",
            re.I,
        ),
        re.compile(
            r"(?:把|将|请|立即|帮我).{0,80}(?:改成|修改|调整|下发|启动|停止)", re.I
        ),
        re.compile(
            r"(?:远程|立即|请|帮我)?.{0,10}(?:复位|重启).{0,20}(?:A\d+|报警|设备|机组)",
            re.I,
        ),
        re.compile(
            r"(?:读取|访问|查询).{0,30}(?:另一个|其他|别的).{0,10}(?:项目|工作区)",
            re.I,
        ),
        re.compile(
            r"\b(?:please\s+)?(?:change|set|start|stop|control|write|delete)\b"
            r".{0,80}\b(?:equipment|unit|setpoint|threshold|data)\b",
            re.I,
        ),
        re.compile(r"\b(?:delete|update|insert|drop|attach|copy)\b", re.I),
    )
    _INFORMATIONAL_PATTERNS = (
        re.compile(
            r"(?:下发|启动|停止|复位|修改|调整).{0,40}"
            r"(?:是否等于|是否意味着|能否证明|为什么)",
            re.I,
        ),
        re.compile(
            r"(?:比较|分析|查看|说明).{0,30}(?:修改|调整|变更).{0,15}(?:前后|历史)",
            re.I,
        ),
        re.compile(
            r"(?:为什么|原因).{0,80}(?:改成|修改|调整|变更).{0,80}"
            r"(?:谁批准|批准|何时生效|何时|前后|效果|变化)",
            re.I,
        ),
    )
    _MIXED_SAFE_UNSAFE_PATTERN = re.compile(
        r"^(?P<safe>.+?)[，,；;。]?\s*(?:然后|随后|同时)\s*(?:直接)?"
        r"(?:把|将|请)?.{0,80}(?:改成|修改|调整|下发|启动|停止)",
        re.I,
    )
    _SAFE_ANALYSIS_INTENT_PATTERN = re.compile(
        r"(?:分析|检查|说明|判断|比较|查询|查看|建议)",
        re.I,
    )
    _EXPLICIT_DATE_PATTERN = re.compile(r"\b20\d{2}[-/.年]\d{1,2}(?:[-/.月]\d{1,2})?")
    _CONFIGURATION_HISTORY_PATTERNS = (
        re.compile(r"(?:当前|批准|有效).{0,30}(?:设定|参数|阈值)", re.I),
        re.compile(r"(?:设定|参数|配置).{0,20}(?:前后|修改|变更)", re.I),
        re.compile(r"旧配置", re.I),
    )
    _CONFIGURATION_DOCUMENT_DISPLAY_PATTERNS = (
        re.compile(
            r"(?:显示|查看|展开).{0,30}(?:当前配置|配置原文|参考.{0,10}配置)",
            re.I,
        ),
    )
    _CONFIGURATION_VALUE_PATTERNS = (
        re.compile(
            r"(?:多少|数值|参数值|设定值|阈值|批准.{0,15}(?:设定|参数))",
            re.I,
        ),
    )
    _COMBINED_EVIDENCE_PATTERNS = (
        re.compile(r"(?:这次|本次|事件).{0,20}除霜.{0,20}(?:符合|合同|时间线)", re.I),
        re.compile(r"除霜.{0,20}(?:符合|合同核对|事件时间线)", re.I),
        re.compile(r"流量证明.{0,30}(?:丢失|发生了什么|控制|反馈)", re.I),
        re.compile(r"(?:报警|排温).{0,25}(?:证明|根因|堵塞|是否|为什么|原因)", re.I),
        re.compile(r"(?:是不是|是否).{0,15}(?:缺冷媒|低效|故障)", re.I),
        re.compile(r"(?:结合|工单).{0,30}(?:遥测|数据|优先检查)", re.I),
        re.compile(r"数据质量.{0,30}(?:效率|比较|影响)", re.I),
        re.compile(r"(?:异常|数据).{0,30}(?:现场确认|仅凭数据|判定)", re.I),
        re.compile(r"旧.{0,20}配置.{0,30}(?:当前|表现|解释)", re.I),
        re.compile(r"(?:为什么|原因).{0,30}(?:数据效果|运行效果|表现)", re.I),
        re.compile(
            r"(?:风机|报警)?.{0,10}[A-Z]\d{3}.{0,20}(?:怎么处理|如何处理|处置|检查)",
            re.I,
        ),
    )
    _DATA_QUALITY_IMPACT_PATTERN = re.compile(
        r"数据质量.{0,30}(?:效率|比较|影响)",
        re.I,
    )
    _SHORT_CYCLING_CONTRACT_PATTERN = re.compile(
        r"(?:停机|启停|启动|短循环).{0,20}(?:频繁|太多|过多|阈值|是否)|"
        r"(?:frequent|too many|threshold).{0,20}(?:starts?|stops?|cycling)",
        re.I,
    )
    _CONFIRMATION_STATUS_PATTERN = re.compile(
        r"(?:是否|是不是).{0,12}已经确认.{0,20}(?:缺冷媒|根因|故障)",
        re.I,
    )
    _REFRIGERANT_DIAGNOSIS_PATTERN = re.compile(
        r"(?:缺冷媒|冷媒不足|制冷剂不足|refrigerant|charge-like)",
        re.I,
    )
    _ENGINEERING_REASON_PATTERN = re.compile(
        r"(?:为什么|原因|依据|如何判断|怎么判断|证据|解释)",
        re.I,
    )
    _PROJECT_METADATA_PATTERN = re.compile(
        r"(?:时区).{0,30}(?:采样间隔)|(?:采样间隔).{0,30}(?:时区)",
        re.I,
    )
    _INTERPOLATION_POLICY_PATTERN = re.compile(
        r"(?:缺失|missing).{0,24}(?:插值|interpolat)|"
        r"(?:插值|interpolat).{0,24}(?:缺失|missing)",
        re.I,
    )
    _CURRENT_MISSING_ROWS_PATTERN = re.compile(
        r"(?:当前|导入|这批|本批|哪些|哪几|具体|实际).{0,32}"
        r"(?:缺失行|缺失记录|缺失样本|缺失点|缺失时间戳|缺失数据|缺口|缺了|"
        r"missing rows?|missing records?|missing samples?|missing timestamps?)|"
        r"(?:缺失行|缺失样本|缺失点|缺失时间戳|缺失数据|missing rows?|"
        r"missing samples?|missing timestamps?).{0,24}(?:当前|导入|这批|本批)",
        re.I,
    )
    _FORMAL_COMPRESSOR_MISMATCH_PATTERN = re.compile(
        r"压缩机.{0,20}(?:有命令|命令).{0,20}(?:没有反馈|无反馈)",
        re.I,
    )
    _EEV_MISMATCH_PATTERN = re.compile(
        r"(?:电子)?膨胀阀.{0,24}"
        r"(?:没有跟随|未跟随|不跟随|不一致|偏差|失配|异常|失败)|"
        r"(?:eev|expansion[ _-]?valve).{0,32}"
        r"(?:mismatch|not follow|failed to follow|deviation|disagree|fault)",
        re.I,
    )
    _GENERIC_CHANGE_COMPARISON_PATTERN = re.compile(
        r"(?:比较|对比).{0,12}(?:修改|变更).{0,5}前后|(?:修改|变更)前后",
        re.I,
    )
    _CHANGE_CONTEXT_PATTERN = re.compile(
        r"(?:修改|变更|调整|设定|参数|配置|change|changed|setpoint|configuration)",
        re.I,
    )
    _EXTREME_QUESTION_PATTERN = re.compile(
        r"(?:最高|最低|最大|最小|极值|maximum|minimum|highest|lowest|extreme)",
        re.I,
    )
    _SNAPSHOT_QUESTION_PATTERN = re.compile(
        r"(?:数据质量|缺失|重复|乱序|冻结|除霜|报警|命令|反馈|启停|风机|流量证明|"
        r"event|alarm|defrost|feedback|short.?cycling|data quality|missing|duplicate|frozen)",
        re.I,
    )
    _ROOT_CAUSE_PROOF_PATTERN = re.compile(
        r"(?:报警|排温|压力|温度).{0,35}(?:证明|证实|根因|缺冷媒|故障)|"
        r"(?:prove|proof|root cause|refrigerant).{0,35}(?:alarm|temperature|pressure|fault)",
        re.I,
    )
    _SCOPE_CLARIFICATION_PATTERN = re.compile(
        r"(?:asset|unit|equipment|time|date|range|alarm code|机组|设备|时间|日期|范围|报警代码)",
        re.I,
    )
    _NON_SCOPE_CLARIFICATION_PATTERN = re.compile(
        r"(?:definition|metric|basis|parameter|configuration|change|rule|contract|"
        r"口径|定义|指标|参数|配置|变更|规则|合同)",
        re.I,
    )
    _CONFIGURATION_EFFECT_PATTERNS = (
        re.compile(
            r"(?:设定|设置|参数|配置).{0,30}(?:前后|修改|变更).{0,30}(?:效果|影响|表现)",
            re.I,
        ),
        re.compile(
            r"(?:改|修改|变更).{0,10}(?:设定|设置|参数|配置).{0,30}(?:效果|影响|表现)",
            re.I,
        ),
        re.compile(r"旧配置.{0,40}(?:当前|效果|影响|表现|运行)", re.I),
        re.compile(
            r"旧(?:的)?.{0,20}(?:设定|设置|参数|配置).{0,40}(?:当前|效果|影响|表现|运行|能耗)",
            re.I,
        ),
    )

    def __init__(
        self,
        toolbox: DirectionToolbox,
        chat_generator: Any,
        budget: AgentBudget = AgentBudget(
            max_steps=11,
            max_tools=10,
            timeout_seconds=180.0,
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

    @classmethod
    def _is_unsafe_request(cls, question: str) -> bool:
        # A mixed request can contain a legitimate historical-analysis phrase and a
        # separate imperative write clause.  The write boundary must win before the
        # informational allowlist is considered, otherwise the safe phrase masks the
        # command that follows it.
        if cls._safe_part_of_mixed_request(question) is not None:
            return True
        if any(pattern.search(question) for pattern in cls._INFORMATIONAL_PATTERNS):
            return False
        return any(pattern.search(question) for pattern in cls._UNSAFE_PATTERNS)

    @classmethod
    def _safe_part_of_mixed_request(cls, question: str) -> str | None:
        match = cls._MIXED_SAFE_UNSAFE_PATTERN.search(question.strip())
        if match is None:
            return None
        safe_part = match.group("safe").strip(" ，,；;。")
        if not cls._SAFE_ANALYSIS_INTENT_PATTERN.search(safe_part):
            return None
        return safe_part

    def _relative_day_clarification(self, question: str) -> str | None:
        if self._EXPLICIT_DATE_PATTERN.search(question):
            return None
        normalized = question.casefold()
        today = datetime.now(self.toolbox.timezone).date()
        if "昨天" in normalized or "昨晚" in normalized or "yesterday" in normalized:
            requested_day = today - timedelta(days=1)
            requested_label = f"自然日昨天（{requested_day.isoformat()}）"
        elif "今天" in normalized or "今日" in normalized or "today" in normalized:
            requested_day = today
            requested_label = f"自然日今天（{requested_day.isoformat()}）"
        else:
            return None
        snapshot_start, snapshot_end = self.toolbox.snapshot_date_bounds()
        if snapshot_start <= requested_day <= snapshot_end:
            return None
        return (
            "### 需要确认日期和判断口径\n\n"
            f"当前导入快照是 **{snapshot_start.isoformat()} 至 "
            f"{snapshot_end.isoformat()}**，不包含{requested_label}。"
            "请确认您是指自然日，还是快照中的最后一天；同时请说明要检查哪台机组，"
            "以及“正常”按报警、启停、控制偏差还是能效判断。"
        )

    @classmethod
    def _generic_change_comparison_clarification(cls, question: str) -> str | None:
        if not cls._GENERIC_CHANGE_COMPARISON_PATTERN.search(question):
            return None
        if re.search(r"HP-\d+", question, re.I):
            return None
        return (
            "### 需要补充比较范围\n\n"
            "请说明要比较的**机组**和**具体变更**；同时给出希望采用的**比较窗口**"
            "（例如变更前后各两小时）以及**指标口径**（送风温度、能耗、能力、COP、"
            "报警或启停）。如果参数名不清楚，可以先提供机组和变更日期。"
        )

    @classmethod
    def _history_supports_generic_change(
        cls,
        history: list[dict[str, str]] | None,
    ) -> bool:
        history_text = " ".join(
            str(turn.get("content", ""))
            for turn in (history or [])[-6:]
            if isinstance(turn, dict)
        )
        if not history_text.strip():
            return False
        has_asset = bool(re.search(r"\bHP-\d+\b", history_text, re.I))
        has_change = bool(cls._CHANGE_CONTEXT_PATTERN.search(history_text))
        has_change_id = bool(re.search(r"\bCR-\d+\b", history_text, re.I))
        has_parameter = bool(
            re.search(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", history_text, re.I)
        )
        return has_change_id or has_parameter or (has_asset and has_change)

    @classmethod
    def _typed_substitute_matches_question(cls, tool_name: str, question: str) -> bool:
        if tool_name == "inspect_metric_extreme":
            return bool(
                cls._EXTREME_QUESTION_PATTERN.search(question)
                or cls._REFRIGERANT_DIAGNOSIS_PATTERN.search(question)
            )
        if tool_name == "inspect_hvac_snapshot":
            return bool(cls._SNAPSHOT_QUESTION_PATTERN.search(question))
        if tool_name == "inspect_configuration_history":
            return cls._requires_configuration_history(question)
        if tool_name == "inspect_configuration_change_effect":
            return cls._requires_configuration_effect(question)
        return False

    @classmethod
    def _should_reject_scope_clarification(
        cls,
        question: str,
        missing: str,
    ) -> bool:
        return bool(
            cls._ROOT_CAUSE_PROOF_PATTERN.search(question)
            and cls._SCOPE_CLARIFICATION_PATTERN.search(missing)
            and not cls._NON_SCOPE_CLARIFICATION_PATTERN.search(missing)
        )

    @classmethod
    def _requires_configuration_history(cls, question: str) -> bool:
        document_display = any(
            pattern.search(question)
            for pattern in cls._CONFIGURATION_DOCUMENT_DISPLAY_PATTERNS
        )
        requests_value = any(
            pattern.search(question) for pattern in cls._CONFIGURATION_VALUE_PATTERNS
        )
        if document_display and not requests_value:
            return False
        return any(
            pattern.search(question) for pattern in cls._CONFIGURATION_HISTORY_PATTERNS
        )

    @classmethod
    def _is_interpolation_policy_only(cls, question: str) -> bool:
        return bool(
            cls._INTERPOLATION_POLICY_PATTERN.search(question)
            and not cls._CURRENT_MISSING_ROWS_PATTERN.search(question)
        )

    @classmethod
    def _requires_combined_evidence(cls, question: str) -> bool:
        if cls._INTERPOLATION_POLICY_PATTERN.search(
            question
        ) and cls._CURRENT_MISSING_ROWS_PATTERN.search(question):
            return True
        if cls._SHORT_CYCLING_CONTRACT_PATTERN.search(question):
            return True
        if cls._CONFIRMATION_STATUS_PATTERN.search(
            question
        ) and not cls._ENGINEERING_REASON_PATTERN.search(question):
            return False
        return any(
            pattern.search(question) for pattern in cls._COMBINED_EVIDENCE_PATTERNS
        )

    @classmethod
    def _requires_configuration_effect(cls, question: str) -> bool:
        return any(
            pattern.search(question) for pattern in cls._CONFIGURATION_EFFECT_PATTERNS
        )

    @staticmethod
    def _focus_named_file_citations(
        question: str,
        citations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized_question = question.casefold()
        named_filenames = list(
            dict.fromkeys(
                str(citation.get("filename", ""))
                for citation in citations
                if str(citation.get("filename", "")).casefold() in normalized_question
            )
        )
        if not named_filenames:
            return citations
        focused = [
            max(
                (
                    citation
                    for citation in citations
                    if str(citation.get("filename", "")) == filename
                ),
                key=lambda citation: len(str(citation.get("excerpt", ""))),
            ).copy()
            for filename in named_filenames
        ]
        base_share, remainder = divmod(100, len(focused))
        for index, citation in enumerate(focused):
            citation["support_share_pct"] = base_share + (1 if index < remainder else 0)
        return focused

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

    async def _answer_exact_named_source(
        self,
        question: str,
    ) -> dict[str, Any] | None:
        if not re.search(
            r"[^\s\"'<>]+\.(?:md|txt|json|csv|pdf|docx|pptx|xlsx|html?|py|toml|ya?ml)\b",
            question,
            re.I,
        ):
            return None
        search = self.toolbox.search_knowledge(question)
        if search.get("clarification") and not search.get("citations"):
            result = self._base(str(search.get("summary", "请补充具体文件路径。")))
            result.update(
                {
                    "mode": "clarification",
                    "model_backed": False,
                    "activities": [
                        {
                            "tool": "ask_for_clarification",
                            "status": "completed",
                            "summary": "需要明确指定文件路径",
                        }
                    ],
                    "clarification": True,
                    "grounding_status": "clarification",
                }
            )
            return result
        named = [
            dict(citation)
            for citation in search.get("citations", [])
            if str(citation.get("filename", "")).casefold() in question.casefold()
        ]
        if not named:
            return None
        evidence_packet = json.dumps(
            {"question": question, "named_file_evidence": named},
            ensure_ascii=False,
            default=str,
        )[:20_000]
        messages = [
            ChatMessage.from_system(
                "Answer the user's exact-file question in concise Chinese Markdown. "
                "Use only named_file_evidence. Lead with the answer. Do not call tools, "
                "invent facts, display internal IDs, or repeat implementation jargon."
            ),
            ChatMessage.from_user(evidence_packet),
        ]
        try:
            run_async = getattr(self.chat_generator, "run_async", None)
            generated = await asyncio.wait_for(
                run_async(messages=messages)
                if callable(run_async)
                else asyncio.to_thread(self.chat_generator.run, messages=messages),
                timeout=min(self.budget.timeout_seconds, 60.0),
            )
        except (TimeoutError, RuntimeError, ValueError):
            return None
        replies = generated.get("replies", [])
        if not isinstance(replies, list) or not replies:
            return None
        reply = replies[-1]
        if not isinstance(reply, ChatMessage) or reply.tool_calls:
            return None
        answer = (reply.text or "").strip()
        if not answer:
            return None
        citations = self._focus_named_file_citations(question, named)
        for citation in citations:
            citation.pop("support_weight", None)
            citation.pop("_exact_filename", None)
        if _unsupported_numeric_claims(
            answer,
            question=question,
            tables=[],
            citations=citations,
        ):
            return None
        result = self._base(answer)
        result.update(
            {
                "mode": "knowledge",
                "citations": citations,
                "activities": [
                    {
                        "tool": "search_project_knowledge",
                        "status": "completed",
                        "summary": "\u5df2\u8bfb\u53d6\u6307\u5b9a\u6587\u4ef6",
                    }
                ],
                "grounding_status": "grounded",
            }
        )
        return result

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
        partial_write_refusal = False
        if self._is_unsafe_request(question):
            safe_part = self._safe_part_of_mixed_request(question)
            if safe_part is None:
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
            question = safe_part
            partial_write_refusal = True

        exact_named_source = await self._answer_exact_named_source(question)
        if exact_named_source is not None:
            return exact_named_source

        relative_day_clarification = self._relative_day_clarification(question)
        if relative_day_clarification is not None:
            result = self._base(relative_day_clarification)
            result.update(
                {
                    "mode": "clarification",
                    "model_backed": False,
                    "activities": [
                        {
                            "tool": "ask_for_clarification",
                            "status": "completed",
                            "summary": "相对日期不在当前导入快照范围内",
                        }
                    ],
                    "clarification": True,
                    "grounding_status": "clarification",
                }
            )
            return result

        generic_change_clarification = (
            None
            if self._history_supports_generic_change(history)
            else self._generic_change_comparison_clarification(question)
        )
        if generic_change_clarification is not None:
            result = self._base(generic_change_clarification)
            result.update(
                {
                    "mode": "clarification",
                    "model_backed": False,
                    "activities": [
                        {
                            "tool": "ask_for_clarification",
                            "status": "completed",
                            "summary": "缺少机组、具体变更、比较窗口和指标口径",
                        }
                    ],
                    "clarification": True,
                    "grounding_status": "clarification",
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
        failed_tools: set[str] = set()
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
                    failed_tools.discard(tool_name)
                elif activity_status == "failed":
                    failed_tools.add(tool_name)
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

        async def resynthesize_with_supplemental_evidence(
            discarded_draft: str,
            *,
            numeric_repair_claims: list[float] | None = None,
        ) -> str | None:
            remaining_seconds = self.budget.timeout_seconds - (monotonic() - started)
            if remaining_seconds <= 0:
                return None
            evidence_packet = json.dumps(
                {
                    "question": question,
                    "discarded_draft": discarded_draft,
                    "structured_results": tables,
                    "project_citations": list(citations.values()),
                    "unsupported_numeric_claims": numeric_repair_claims or [],
                },
                ensure_ascii=False,
                default=str,
            )[:16_000]
            synthesis_messages = [
                ChatMessage.from_system(
                    (
                        "NUMERIC_GROUNDING_REPAIR. The previous draft used unsupported or "
                        "mislabelled numbers. Rewrite one concise Chinese Markdown answer "
                        "using only the supplied structured results and project citations. "
                        "Use the exact delta summary when present; distinguish absolute "
                        "change, relative percent and percentage points. Omit any number "
                        "that is not directly supported or arithmetically derivable. Do not "
                        "request or call tools."
                        if numeric_repair_claims
                        else (
                            "The previous draft is discarded because it did not see the "
                            "required supplemental project evidence. Re-synthesize one "
                            "concise Chinese Markdown answer using only the supplied "
                            "structured results and project citations. Do not invent "
                            "numbers and do not request or call tools."
                        )
                    )
                ),
                ChatMessage.from_user(evidence_packet),
            ]

            async def run_generator() -> dict[str, Any]:
                run_async = getattr(self.chat_generator, "run_async", None)
                if callable(run_async):
                    return await run_async(messages=synthesis_messages)
                return await asyncio.to_thread(
                    self.chat_generator.run,
                    messages=synthesis_messages,
                )

            generated = await asyncio.wait_for(
                run_generator(),
                timeout=remaining_seconds,
            )
            replies = generated.get("replies", [])
            if not isinstance(replies, list) or not replies:
                return None
            message = replies[-1]
            if not isinstance(message, ChatMessage) or message.tool_calls:
                return None
            return (message.text or "").strip() or None

        def database_payload(
            sql: str,
            title: str,
            chart_kind: str,
            x_column: str,
            y_column: str,
        ) -> dict[str, Any]:
            if self._is_interpolation_policy_only(question):
                return {
                    "summary": (
                        "Interpolation policy is defined by the project SOP; use project "
                        "knowledge only unless the user asks which imported rows are missing."
                    ),
                    "activity_summary": "插值政策问题只需要核对项目 SOP",
                    "activity_status": "rejected",
                    "retryable": False,
                }
            if self._PROJECT_METADATA_PATTERN.search(question):
                return {
                    "summary": (
                        "Project timezone and sample interval are import metadata; "
                        "use project knowledge only and do not query telemetry."
                    ),
                    "activity_summary": "项目元数据问题不需要查询遥测表",
                    "activity_status": "rejected",
                    "retryable": False,
                }
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
                    "activity_summary": (
                        "只读查询未通过安全策略："
                        f"{type(exc).__name__}: {str(exc)[:120]}"
                    ),
                    "activity_status": "rejected",
                    "retryable": True,
                }

        def snapshot_payload(
            operation: str,
            asset_id: str,
            event_type: str,
            event_types: list[str] | None,
            start_time: str,
            end_time: str,
            alarm_code: str,
        ) -> dict[str, Any]:
            if self._is_interpolation_policy_only(question):
                return {
                    "summary": (
                        "Interpolation policy is defined by the project SOP; use project "
                        "knowledge only unless the user asks which imported rows are missing."
                    ),
                    "activity_summary": "插值政策问题只需要核对项目 SOP",
                    "activity_status": "rejected",
                    "retryable": False,
                }
            if self._PROJECT_METADATA_PATTERN.search(question):
                return {
                    "summary": (
                        "Project timezone and sample interval are import metadata; "
                        "use project knowledge only and do not inspect runtime events."
                    ),
                    "activity_summary": "项目元数据问题不需要盘点运行快照",
                    "activity_status": "rejected",
                    "retryable": False,
                }
            if (
                self._FORMAL_COMPRESSOR_MISMATCH_PATTERN.search(question)
                and "流量证明" not in question
            ):
                event_type = "compressor_feedback_mismatch"
                event_types = ["compressor_feedback_mismatch"]
            elif self._EEV_MISMATCH_PATTERN.search(question):
                operation = "control_events"
                event_type = "eev_feedback_mismatch"
                event_types = ["eev_feedback_mismatch"]
            try:
                return self.toolbox.inspect_snapshot(
                    operation,
                    asset_id,
                    event_type,
                    event_types,
                    start_time,
                    end_time,
                    alarm_code,
                )
            except SnapshotFilterError as exc:
                return {
                    "summary": (
                        "The typed snapshot filter was rejected. Use exact allowlisted "
                        "event types. Only pass start_time/end_time when a full ISO date "
                        "and time is known; otherwise leave both empty and filter by "
                        f"asset/event. Reason: {type(exc).__name__}: {str(exc)[:240]}"
                    ),
                    "activity_summary": "快照筛选参数无效，已要求模型修正",
                    "activity_status": "rejected",
                    "retryable": True,
                }
            except (ValueError, duckdb.Error) as exc:
                return {
                    "summary": (
                        "The typed snapshot inspection failed closed because its "
                        "read-only database operation could not complete. Do not remove "
                        "the user's asset or time boundaries and do not form a data "
                        f"conclusion. Reason: {type(exc).__name__}: {str(exc)[:240]}"
                    ),
                    "activity_summary": "快照数据库执行失败，已停止本次数据分析",
                    "activity_status": "failed",
                    "retryable": False,
                }

        def knowledge_payload(query: str) -> dict[str, Any]:
            if self._requires_configuration_effect(
                question
            ) and not self._ENGINEERING_REASON_PATTERN.search(question):
                return {
                    "summary": (
                        "Pure configuration-effect comparison is already covered by "
                        "inspect_configuration_change_effect; do not add document search."
                    ),
                    "activity_summary": "纯配置效果问题不需要重复检索资料",
                    "activity_status": "rejected",
                    "retryable": False,
                }
            return self.toolbox.search_knowledge(query)

        def configuration_history_payload(
            asset_id: str,
            parameter_name: str,
        ) -> dict[str, Any]:
            if self._requires_configuration_effect(question):
                return {
                    "summary": (
                        "Configuration change effect already includes the approved "
                        "effective boundary; do not call configuration history separately."
                    ),
                    "activity_summary": "配置效果工具已包含生效边界，拒绝重复历史查询",
                    "activity_status": "rejected",
                    "retryable": False,
                }
            return self.toolbox.inspect_configuration_history(
                asset_id,
                parameter_name,
            )

        def clarification_payload(missing: str) -> dict[str, Any]:
            if self._should_reject_scope_clarification(question, missing):
                return {
                    "summary": (
                        "This engineering-evidence question must use the current imported "
                        "snapshot when asset or time is omitted. Do not ask for asset/time; "
                        "continue with project knowledge plus one bounded read-only data "
                        "query and state that telemetry cannot prove a physical root cause."
                    ),
                    "activity_summary": "全局工程判断默认扫描当前导入快照，已拒绝不必要的澄清",
                    "activity_status": "rejected",
                    "retryable": True,
                }
            return {
                "summary": f"请补充：{missing}",
                "clarification": True,
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
                    lambda: knowledge_payload(query),
                ),
            ),
            Tool(
                name="query_hvac_database",
                description=(
                    "Run one bounded read-only DuckDB SELECT over exactly one approved table. "
                    "The SQL policy permits one flat SELECT only: no WITH/CTE, "
                    "subquery, window function, SELECT star, or file access. Use "
                    "aggregate SQL for large time ranges. Samples are "
                    f"{self.toolbox.sample_interval_seconds} seconds apart, so "
                    "electrical energy kWh is SUM(electric_power_kw * "
                    f"{self.toolbox.sample_interval_seconds} / 3600). Use telemetry_raw for "
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
                name="inspect_hvac_snapshot",
                description=(
                    "Run one typed read-only HVAC snapshot inspection instead of "
                    "writing SQL. Use data_quality for completeness, missing samples, "
                    "duplicate timestamps, ingest-order reversals and frozen sensor "
                    "tuples; control_events for command-feedback mismatches, short "
                    "cycling, flow-proof loss and defrost; alarm_events for alarm-code "
                    "windows and observed command/feedback values. Prefer this tool for "
                    "event segmentation and snapshot-wide audits. Choose exactly one "
                    "inspection operation per question unless the user explicitly asks "
                    "for a broad audit. For a named event timeline, pass asset_id, "
                    "event_types and optional start_time/end_time filters; use "
                    "event_types=[defrost] for defrost contract or timeline questions. "
                    "For codes such as A311, use operation=alarm_events and pass "
                    "alarm_code; then use the returned asset/event to search work orders."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": [
                                "data_quality",
                                "control_events",
                                "alarm_events",
                            ],
                        },
                        "asset_id": {"type": "string"},
                        "event_type": {"type": "string"},
                        "event_types": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "start_time": {"type": "string"},
                        "end_time": {"type": "string"},
                        "alarm_code": {"type": "string"},
                    },
                    "required": ["operation"],
                    "additionalProperties": False,
                },
                function=lambda operation, asset_id="", event_type="", event_types=None, start_time="", end_time="", alarm_code="": (
                    invoke(
                        "inspect_hvac_snapshot",
                        lambda: snapshot_payload(
                            operation,
                            asset_id,
                            event_type,
                            event_types,
                            start_time,
                            end_time,
                            alarm_code,
                        ),
                    )
                ),
            ),
            Tool(
                name="inspect_configuration_history",
                description=(
                    "Read current and historical approved configuration rows with "
                    "effective dates and source files. Use this together with project "
                    "document search for current, approved, effective, superseded or "
                    "before/after configuration questions. Do not write SQL against "
                    "config_history when this typed tool can answer the question."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "asset_id": {"type": "string"},
                        "parameter_name": {"type": "string"},
                    },
                    "required": ["asset_id", "parameter_name"],
                    "additionalProperties": False,
                },
                function=lambda asset_id, parameter_name: invoke(
                    "inspect_configuration_history",
                    lambda: configuration_history_payload(
                        asset_id,
                        parameter_name,
                    ),
                ),
            ),
            Tool(
                name="inspect_configuration_change_effect",
                description=(
                    "Compare the two hours immediately before and after the latest "
                    "approved supply-air setpoint change for one asset. Returns the "
                    "approved change boundary, average setpoint, supply temperature, "
                    "electrical energy, thermal output and COP. Use this for questions "
                    "about the observed effect of changing a setpoint; do not hand-write "
                    "SQL or call inspect_configuration_history separately."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "asset_id": {"type": "string"},
                        "parameter_name": {
                            "type": "string",
                            "enum": ["supply_air_sp_c"],
                        },
                    },
                    "required": ["asset_id", "parameter_name"],
                    "additionalProperties": False,
                },
                function=lambda asset_id, parameter_name: invoke(
                    "inspect_configuration_change_effect",
                    lambda: self.toolbox.inspect_configuration_change_effect(
                        asset_id,
                        parameter_name,
                    ),
                ),
            ),
            Tool(
                name="inspect_metric_extreme",
                description=(
                    "Locate the exact minimum or maximum of one allowlisted HVAC "
                    "telemetry metric over the whole imported snapshot or one asset. "
                    "Returns the observed extreme window, sample count, duration, "
                    "thermal output, COP and superheat. Use after resolving controller "
                    "aliases such as P_SUC. This reports observations, not an invented "
                    "alarm threshold."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "metric": {
                            "type": "string",
                            "enum": sorted(HVACSnapshotInspector.METRICS),
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["minimum", "maximum"],
                        },
                        "asset_id": {"type": "string"},
                    },
                    "required": ["metric", "direction", "asset_id"],
                    "additionalProperties": False,
                },
                function=lambda metric, direction, asset_id: invoke(
                    "inspect_metric_extreme",
                    lambda: self.toolbox.inspect_metric_extreme(
                        metric,
                        direction,
                        asset_id,
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
                    lambda: clarification_payload(missing),
                ),
            ),
        ]
        runner = Agent(
            chat_generator=self.chat_generator,
            tools=tools,
            system_prompt=(
                "你是商用空调项目助手。只依据工具返回的项目资料和数据库结果回答。"
                "先判断需要查知识、查数据还是两者都查；事实问题必须调用工具。"
                "涉及当前、有效、批准、配置版本或单纯变更原因的问题，必须查询项目资料；"
                "不涉及运行效果时，使用 inspect_configuration_history 核对生效边界。"
                "涉及改设定前后效果或配置影响的问题，必须使用 "
                "inspect_configuration_change_effect；该工具已包含生效边界和遥测聚合，"
                "不要再手写 SQL、重复调用 inspect_configuration_history，"
                "也不要再调用 inspect_hvac_snapshot。"
                "纯效果对比不必额外检索资料；同时询问为什么、原因或工程解释时，"
                "再加一次 search_project_knowledge。"
                "遥测是现场观测值，不能覆盖批准配置，只能用于核对实际执行与效果。"
                "哪台、哪里、各机组、最高、多久、哪一段或哪个事件这类全局盘点题，"
                "在未指定时间时默认扫描当前导入快照，并说明可用起止时间和时区；"
                "不得只因缺少时间范围而澄清。"
                "用户问哪台机组更节能且未给口径时，不要澄清；默认扫描完整快照，"
                "按总热量除以总电耗计算负荷加权 COP，并明确窗口、时区、口径和限制。"
                "用户已给机组但问改参数前后、参数名称未写明时，不要澄清；"
                "先检索配置资料，再调用 inspect_configuration_history，"
                "将 parameter_name 传空字符串以列出该机组全部批准变更。"
                "只要求配置值前后表格时，不要调用 "
                "inspect_configuration_change_effect；只有询问运行效果时才调用它。"
                "用户说‘显示你参考的当前配置原文’时，直接检索并展示当前配置文件、"
                "位置、状态和原文片段，不要澄清。"
                "为什么、是否符合、如何处理或根因是否成立这类工程判断，"
                "必须同时查询项目资料和数据库；仅资料或仅数据都不足。"
                "根因是否成立、报警能否证明故障这类全局判断，即使未指定机组或时间，"
                "也必须扫描当前导入快照并说明证据限制，不得只因缺少机组或时间而澄清。"
                "机组清单和控制器映射应优先查询项目资料中的资产台账。"
                "数据质量、命令反馈、启停、报警或状态事件问题，"
                "优先使用 inspect_hvac_snapshot，避免现场拼接复杂 SQL；"
                "但‘数据缺失能否直接插值’这类规则问题只检索项目 SOP，"
                "除非用户明确询问当前导入数据具体缺了哪些行，否则不要盘点快照或写 SQL；"
                "每个问题最多选择一个快照检查操作，除非用户明确要求全量综合审计。"
                "遇到 A311 这类报警码时，先调用 inspect_hvac_snapshot(operation=alarm_events, "
                "alarm_code=A311) 识别机组、持续时间和命令反馈，再用识别出的机组与风机事件检索工单和 SOP；"
                "不要把报警码当作设备编号，也不要在已返回报警事件时继续澄清。"
                "除霜合同核对或事件时间线必须先检索控制合同，再调用 "
                "inspect_hvac_snapshot(operation=control_events, event_types=[defrost])，"
                "已知机组时同时传 asset_id；工具会返回持续时间、压缩机反馈、"
                "室外风机反馈、盘管升温和图表，成功后不要再写 SQL。"
                "流量证明丢失后的控制与反馈问题也必须同时检索控制合同和使用一次"
                "带 asset_id 的 control_events 快照检查，并传 "
                "event_types=[flow_proof_loss, compressor_feedback_mismatch_observation]，"
                "只有用户给出完整 ISO 日期时间时才传 start_time/end_time；"
                "若只给 10:20 这类时钟时间则将二者留空；"
                "成功后直接回答命令、反馈、报警和随后恢复，不要再写 SQL。"
                "用户问报警最多时，按 inspect_hvac_snapshot 返回的连续报警事件条数统计，"
                "不要按报警样本数或持续时间排名；事件数并列时必须明确并列，"
                "不要再写 SQL 改换口径。"
                "项目资料和一个数据工具已经提供足够证据时，立即形成最终回答，"
                "不要重复检索、追加摘要查询或调用其他快照检查。"
                "用户明确要求数据质量、能耗、报警、启停、命令反馈和维修解释等综合盘点时，"
                "允许依次完成一次数据质量、一次报警、一次控制事件、一次综合聚合查询，"
                "但最多两次资料检索；证据齐全后必须立即形成最终回答，不得重复检索同一资料。"
                "遇到 P_SUC 等控制器别名时，先查 point_aliases，"
                "再用 canonical 字段查询 telemetry_clean；不要查询无关配置历史。"
                "用户询问最低、最高或极值窗口时，优先使用 inspect_metric_extreme；"
                "用户明确要求趋势图，且机组和指标已经明确但时间范围未给出时，"
                "先在可用数据范围内定位异常窗口并作图，同时说明采用的数据范围和时区；"
                "异常趋势优先使用一次平铺聚合查询：用 date_trunc('minute', timestamp) "
                "作为 period，并对指标取 max，按 period 分组排序且最多返回 50 行；"
                "不要使用 strftime；图表查询成功后直接形成最终回答，不再重复查询摘要。"
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

        data_tools = {
            "query_hvac_database",
            "inspect_hvac_snapshot",
            "inspect_configuration_history",
            "inspect_configuration_change_effect",
            "inspect_metric_extreme",
        }
        failed_data_tools = data_tools & failed_tools
        if failed_data_tools:
            activities.append(
                {
                    "tool": "agent",
                    "status": "failed",
                    "summary": (
                        "数据工具执行失败且未被后续成功重试，已失败关闭："
                        f"{', '.join(sorted(failed_data_tools))}"
                    ),
                }
            )
            result = self._base(
                "### 数据分析未完成\n\n"
                "本次只读数据工具执行失败，且没有取得可复核的后续成功结果。"
                "项目资料不能替代本次运行数据，因此回答已被拦截，请重试。"
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

        used_data_tool = bool(data_tools & used_tools)
        if (
            "query_hvac_database" in attempted_tools
            and "query_hvac_database" not in used_tools
            and not (
                self._PROJECT_METADATA_PATTERN.search(question)
                or self._is_interpolation_policy_only(question)
            )
        ):
            rejected_query_indices = [
                index
                for index, activity in enumerate(activities)
                if activity["tool"] == "query_hvac_database"
                and activity["status"] == "rejected"
            ]
            recovered_after_rejection = bool(
                rejected_query_indices
                and any(
                    index > max(rejected_query_indices)
                    and activity["status"] == "completed"
                    and self._typed_substitute_matches_question(
                        activity["tool"], question
                    )
                    for index, activity in enumerate(activities)
                )
            )
            if recovered_after_rejection:
                pass
            else:
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

        if (
            not clarification
            and "search_project_knowledge" not in used_tools
            and (
                (self._requires_combined_evidence(question) and used_data_tool)
                or (
                    self._requires_configuration_history(question)
                    and "inspect_configuration_history" in used_tools
                )
            )
        ):
            fallback_query = question
            if "数据质量" in question and "效率" in question:
                fallback_query = (
                    "data analysis SOP missing timestamps duplicate keys ingest order "
                    "frozen sensor efficiency comparison"
                )
            elif self._INTERPOLATION_POLICY_PATTERN.search(
                question
            ) and self._CURRENT_MISSING_ROWS_PATTERN.search(question):
                fallback_query = (
                    "data analysis SOP missing rows interpolation operating-state "
                    "sample interval"
                )
            elif self._SHORT_CYCLING_CONTRACT_PATTERN.search(question):
                fallback_query = (
                    "short cycling starts per hour threshold current configuration"
                )
            try:
                invoke(
                    "search_project_knowledge",
                    lambda: self.toolbox.search_knowledge(fallback_query),
                )
            except AgentBudgetError:
                pass
            if "search_project_knowledge" in used_tools:
                try:
                    regenerated_answer = await resynthesize_with_supplemental_evidence(
                        answer
                    )
                except (TimeoutError, RuntimeError, ValueError) as exc:
                    logger.warning(
                        "Supplemental grounding synthesis failed closed: %s",
                        type(exc).__name__,
                    )
                    regenerated_answer = None
                if regenerated_answer is None:
                    activities.append(
                        {
                            "tool": "agent",
                            "status": "failed",
                            "summary": "补充项目证据后未能重新综合最终回答，已失败关闭",
                        }
                    )
                    result = self._base(
                        "### 工程判断证据未完成\n\n"
                        "已补充检索项目资料，但模型没有基于新增证据重新形成可复核回答。"
                        "为避免把事后附加引用误当成答案依据，本次回答已被拦截，请重试。"
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
                answer = regenerated_answer
                activities.append(
                    {
                        "tool": "agent",
                        "status": "completed",
                        "summary": "已让模型基于补充的项目资料重新综合最终回答",
                    }
                )

        if (
            not clarification
            and self._DATA_QUALITY_IMPACT_PATTERN.search(question)
            and "search_project_knowledge" in used_tools
            and not used_data_tool
        ):
            try:
                invoke(
                    "inspect_hvac_snapshot",
                    lambda: snapshot_payload(
                        "data_quality",
                        "",
                        "",
                        None,
                        "",
                        "",
                        "",
                    ),
                )
            except AgentBudgetError:
                pass
            used_data_tool = bool(data_tools & used_tools)
            if used_data_tool:
                try:
                    regenerated_answer = await resynthesize_with_supplemental_evidence(
                        answer
                    )
                except (TimeoutError, RuntimeError, ValueError) as exc:
                    logger.warning(
                        "Supplemental data synthesis failed closed: %s",
                        type(exc).__name__,
                    )
                    regenerated_answer = None
                if regenerated_answer is None:
                    activities.append(
                        {
                            "tool": "agent",
                            "status": "failed",
                            "summary": "补充运行数据后未能重新综合最终回答，已失败关闭",
                        }
                    )
                    result = self._base(
                        "### 工程判断证据未完成\n\n"
                        "已补充盘点当前运行数据，但模型没有基于项目资料和新增数据重新形成可复核回答。"
                        "本次回答已被拦截，请重试。"
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
                answer = regenerated_answer
                activities.append(
                    {
                        "tool": "agent",
                        "status": "completed",
                        "summary": "已让模型基于补充的运行数据重新综合最终回答",
                    }
                )

        if (
            self._requires_configuration_effect(question)
            and "inspect_configuration_change_effect" not in used_tools
        ):
            activities.append(
                {
                    "tool": "agent",
                    "status": "failed",
                    "summary": "配置效果问题未完成专用前后窗口核对，已失败关闭",
                }
            )
            result = self._base(
                "### 配置效果核对未完成\n\n"
                "这个问题要求比较配置变更前后的运行效果，但本次没有完成受控的生效边界、"
                "完整两小时窗口和遥测指标核对。为避免编造效果数据，回答已被拦截，请重试。"
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
            self._requires_combined_evidence(question)
            and not clarification
            and ("search_project_knowledge" not in used_tools or not used_data_tool)
        ):
            activities.append(
                {
                    "tool": "agent",
                    "status": "failed",
                    "summary": "工程判断未同时取得项目资料和运行数据，已失败关闭",
                }
            )
            result = self._base(
                "### 工程判断证据不足\n\n"
                "这个问题需要同时核对项目资料和运行数据，但本次只完成了其中一类证据。"
                "为避免把通用知识、历史记录或单段遥测误当成完整结论，回答已被拦截，请重试。"
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
            self._requires_configuration_history(question)
            and not {
                "inspect_configuration_history",
                "inspect_configuration_change_effect",
            }
            & used_tools
        ):
            activities.append(
                {
                    "tool": "agent",
                    "status": "failed",
                    "summary": "配置问题未核对受控配置历史，已失败关闭",
                }
            )
            result = self._base(
                "### 配置核对未完成\n\n"
                "这个问题涉及当前、批准或前后配置，但本次没有完成配置历史与生效边界核对。"
                "为避免把旧值当成当前值，回答已被拦截，请重试。"
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

        unsupported_numeric_claims = (
            []
            if clarification
            else _unsupported_numeric_claims(
                answer,
                question=question,
                tables=tables,
                citations=list(citations.values()),
            )
        )
        if unsupported_numeric_claims:
            try:
                repaired_answer = await resynthesize_with_supplemental_evidence(
                    answer,
                    numeric_repair_claims=unsupported_numeric_claims,
                )
            except (TimeoutError, RuntimeError, ValueError) as exc:
                logger.warning(
                    "Numeric grounding repair failed closed: %s",
                    type(exc).__name__,
                )
                repaired_answer = None
            if repaired_answer is not None:
                repaired_claims = _unsupported_numeric_claims(
                    repaired_answer,
                    question=question,
                    tables=tables,
                    citations=list(citations.values()),
                )
                if not repaired_claims:
                    answer = repaired_answer
                    unsupported_numeric_claims = []
                    activities.append(
                        {
                            "tool": "agent",
                            "status": "completed",
                            "summary": "已基于同一结构化证据修正未通过数值核对的草稿",
                        }
                    )
                else:
                    unsupported_numeric_claims = repaired_claims

        if unsupported_numeric_claims:
            activities.append(
                {
                    "tool": "agent",
                    "status": "failed",
                    "summary": (
                        "最终回答包含未被结构化结果或引用支持的数值，已失败关闭："
                        f"{unsupported_numeric_claims[:8]}"
                    ),
                }
            )
            result = self._base(
                "### 数值核对未通过\n\n"
                "最终回答中的数值无法与本次只读查询或引用资料相互核对。"
                "为避免输出未经证实的工程数据，本次回答已被拦截，请重试。"
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

        if "search_project_knowledge" in used_tools and used_data_tool:
            mode = "combined"
        elif used_data_tool:
            mode = "data"
        elif "ask_for_clarification" in used_tools:
            mode = "clarification"
        else:
            mode = "knowledge"
        if partial_write_refusal:
            answer = (
                f"{answer}\n\n### 未执行写入\n\n"
                "已完成上述只读分析；未执行阈值写入或现场设备控制。"
            )
        result = self._base(answer)
        result.update(
            {
                "mode": mode,
                "tables": tables,
                "charts": charts,
                "citations": self._focus_named_file_citations(
                    question,
                    self._normalize_citations(citations),
                ),
                "activities": activities,
                "clarification": clarification,
                "partial_refusal": partial_write_refusal,
                "grounding_status": ("clarification" if clarification else "grounded"),
            }
        )
        return result

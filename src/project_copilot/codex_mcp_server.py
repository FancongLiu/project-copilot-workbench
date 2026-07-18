from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import duckdb
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
import sqlglot


ASSET_DIR = Path(__file__).resolve().parent / "codex_assets"
OPERATIONS = {
    "schema": ASSET_DIR / "schema.sql",
    "data_quality": ASSET_DIR / "data-quality.sql",
    "cop_ranking": ASSET_DIR / "cop-ranking.sql",
}
READ_ONLY_TOOL = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

ChartKind = Literal["none", "line", "bar"]
InspectionKind = Literal["data_quality", "control_events", "alarm_events"]
EventType = Literal[
    "",
    "coverage",
    "missing_samples",
    "duplicate_rows",
    "duplicate_timestamp",
    "duplicate_keys",
    "out_of_order",
    "frozen_sensor_tuple",
    "frozen_sensor_tuples",
    "telemetry_freeze",
    "defrost",
    "flow_proof_loss",
    "compressor_feedback_mismatch",
    "compressor_feedback_mismatch_observation",
    "eev_feedback_mismatch",
    "eev_command_feedback_mismatch",
    "eev_feedback_mismatch_observation",
    "fan_feedback_mismatch",
    "fan_command_feedback_mismatch",
    "indoor_fan_feedback_mismatch",
    "outdoor_fan_feedback_mismatch",
    "indoor_fan_command_feedback_mismatch",
    "outdoor_fan_command_feedback_mismatch",
    "discharge_temperature_alarm",
    "high_discharge_temperature",
]
ExtremeDirection = Literal["minimum", "maximum"]
ConfigurationEffectParameter = Literal["supply_air_sp_c"]
MetricName = Literal[
    "cop",
    "discharge_pressure_kpa_g",
    "discharge_temp_c",
    "electric_power_kw",
    "subcooling_k",
    "suction_pressure_kpa_g",
    "suction_temp_c",
    "superheat_k",
    "thermal_output_kw",
]


def _database_from_environment() -> Path:
    value = os.environ.get("PROJECT_COPILOT_MCP_DATABASE", "").strip()
    if not value:
        raise RuntimeError("Governed MCP database is not configured")
    database = Path(value).resolve()
    if not database.is_file():
        raise RuntimeError("Governed MCP database is unavailable")
    return database


def _corpus_from_environment() -> Path:
    value = os.environ.get("PROJECT_COPILOT_MCP_CORPUS", "").strip()
    if not value:
        raise RuntimeError("Governed MCP corpus is not configured")
    corpus = Path(value).resolve()
    if (
        not (corpus / "manifest.json").is_file()
        or not (corpus / "datasets" / "hvac_bakeoff.duckdb").is_file()
        or not (corpus / "docs" / "source").is_dir()
    ):
        raise RuntimeError("Governed MCP corpus is unavailable")
    return corpus


@lru_cache(maxsize=4)
def _toolbox(corpus: str) -> Any:
    from project_copilot.direction import DirectionToolbox

    return DirectionToolbox(corpus)


def run_operation(operation: str, database: str | Path) -> list[dict[str, Any]]:
    sql_path = OPERATIONS.get(operation)
    if sql_path is None:
        raise ValueError(f"Unsupported governed operation: {operation}")
    database_path = Path(database).resolve()
    if not database_path.is_file():
        raise ValueError("Governed database is unavailable")
    expressions = sqlglot.parse(sql_path.read_text(encoding="utf-8"), read="duckdb")
    connection = duckdb.connect(
        str(database_path),
        read_only=True,
        config={
            "enable_external_access": "false",
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
            "allow_community_extensions": "false",
            "memory_limit": "256MB",
            "threads": "2",
            "max_temp_directory_size": "0GB",
            "lock_configuration": "true",
        },
    )
    rows: list[dict[str, Any]] = []
    try:
        for expression in expressions:
            cursor = connection.execute(expression.sql(dialect="duckdb"))
            columns = [column[0] for column in cursor.description]
            rows.extend(
                dict(zip(columns, values, strict=True)) for values in cursor.fetchall()
            )
    finally:
        connection.close()
    return rows


def run_typed_operation(
    operation: str,
    corpus: str | Path,
    **arguments: Any,
) -> dict[str, Any]:
    toolbox = _toolbox(str(Path(corpus).resolve()))
    if operation == "search_project_knowledge":
        return toolbox.search_knowledge(str(arguments.get("query", "")))
    if operation == "query_hvac_database":
        return toolbox.query_database(
            sql=str(arguments.get("sql", "")),
            title=str(arguments.get("title", "")),
            chart_kind=str(arguments.get("chart_kind", "none")),
            x_column=str(arguments.get("x_column", "")),
            y_column=str(arguments.get("y_column", "")),
        )
    if operation == "inspect_hvac_snapshot":
        event_types = arguments.get("event_types")
        if event_types is not None and not isinstance(event_types, list):
            raise ValueError("event_types must be a list")
        return toolbox.inspect_snapshot(
            str(arguments.get("inspection", "")),
            str(arguments.get("asset_id", "")),
            str(arguments.get("event_type", "")),
            event_types,
            str(arguments.get("start_time", "")),
            str(arguments.get("end_time", "")),
            str(arguments.get("alarm_code", "")),
        )
    if operation == "inspect_configuration_history":
        return toolbox.inspect_configuration_history(
            str(arguments.get("asset_id", "")),
            str(arguments.get("parameter_name", "")),
        )
    if operation == "inspect_configuration_change_effect":
        return toolbox.inspect_configuration_change_effect(
            str(arguments.get("asset_id", "")),
            str(arguments.get("parameter_name", "")),
        )
    if operation == "inspect_metric_extreme":
        return toolbox.inspect_metric_extreme(
            str(arguments.get("metric", "")),
            str(arguments.get("direction", "")),
            str(arguments.get("asset_id", "")),
        )
    raise ValueError(f"Unsupported governed typed operation: {operation}")


mcp = FastMCP(
    "Project Copilot governed HVAC evidence",
    instructions=(
        "Read-only bounded operations over one private synthetic HVAC corpus. "
        "Typed inspections are preferred. The SQL tool accepts only one guarded "
        "SELECT over allowlisted local tables and never accepts a filesystem path."
    ),
)


@mcp.tool(
    name="schema",
    description="Return the bounded table and column inventory for the synthetic snapshot.",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def schema() -> list[dict[str, Any]]:
    return run_operation("schema", _database_from_environment())


@mcp.tool(
    name="data_quality",
    description=(
        "Return fixed coverage, duplicate, ingest-order and frozen-sensor checks "
        "for the synthetic telemetry snapshot."
    ),
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def data_quality() -> list[dict[str, Any]]:
    return run_operation("data_quality", _database_from_environment())


@mcp.tool(
    name="cop_ranking",
    description=(
        "Return the fixed load-weighted COP aggregation by asset for the synthetic snapshot."
    ),
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def cop_ranking() -> list[dict[str, Any]]:
    return run_operation("cop_ranking", _database_from_environment())


@mcp.tool(
    name="search_project_knowledge",
    description=(
        "Search approved project documents, configurations, meetings, decisions, "
        "work orders and SOPs. Returns exact original filenames and excerpts."
    ),
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def search_project_knowledge(query: str) -> dict[str, Any]:
    if not query.strip() or len(query) > 500:
        raise ValueError("Knowledge query must contain 1 to 500 characters")
    return run_typed_operation(
        "search_project_knowledge",
        _corpus_from_environment(),
        query=query,
    )


@mcp.tool(
    name="query_hvac_database",
    description=(
        "Run one bounded read-only SELECT over one approved HVAC table. No CTE, "
        "subquery, SELECT star, file access, write statement or extension is allowed."
    ),
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def query_hvac_database(
    sql: str,
    title: str = "查询结果",
    chart_kind: ChartKind = "none",
    x_column: str = "",
    y_column: str = "",
) -> dict[str, Any]:
    return run_typed_operation(
        "query_hvac_database",
        _corpus_from_environment(),
        sql=sql,
        title=title,
        chart_kind=chart_kind,
        x_column=x_column,
        y_column=y_column,
    )


@mcp.tool(
    name="inspect_hvac_snapshot",
    description=(
        "Inspect data_quality, control_events, or alarm_events with bounded filters."
    ),
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def inspect_hvac_snapshot(
    inspection: InspectionKind,
    asset_id: str = "",
    event_type: EventType = "",
    event_types: list[EventType] | None = None,
    start_time: str = "",
    end_time: str = "",
    alarm_code: str = "",
) -> dict[str, Any]:
    return run_typed_operation(
        "inspect_hvac_snapshot",
        _corpus_from_environment(),
        inspection=inspection,
        asset_id=asset_id,
        event_type=event_type,
        event_types=event_types,
        start_time=start_time,
        end_time=end_time,
        alarm_code=alarm_code,
    )


@mcp.tool(
    name="inspect_configuration_history",
    description="Read approved current and historical configuration rows.",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def inspect_configuration_history(
    asset_id: str,
    parameter_name: str,
) -> dict[str, Any]:
    return run_typed_operation(
        "inspect_configuration_history",
        _corpus_from_environment(),
        asset_id=asset_id,
        parameter_name=parameter_name,
    )


@mcp.tool(
    name="inspect_configuration_change_effect",
    description=(
        "Compare the two hours before and after the latest approved supply-air "
        "setpoint change for one asset."
    ),
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def inspect_configuration_change_effect(
    asset_id: str,
    parameter_name: ConfigurationEffectParameter,
) -> dict[str, Any]:
    return run_typed_operation(
        "inspect_configuration_change_effect",
        _corpus_from_environment(),
        asset_id=asset_id,
        parameter_name=parameter_name,
    )


@mcp.tool(
    name="inspect_metric_extreme",
    description="Locate an allowlisted HVAC metric minimum or maximum window.",
    annotations=READ_ONLY_TOOL,
    structured_output=True,
)
def inspect_metric_extreme(
    metric: MetricName,
    direction: ExtremeDirection,
    asset_id: str = "",
) -> dict[str, Any]:
    return run_typed_operation(
        "inspect_metric_extreme",
        _corpus_from_environment(),
        metric=metric,
        direction=direction,
        asset_id=asset_id,
    )


def main() -> None:
    if os.environ.get("PROJECT_COPILOT_MCP_CORPUS", "").strip():
        _toolbox(str(_corpus_from_environment()))
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

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


def _database_from_environment() -> Path:
    value = os.environ.get("PROJECT_COPILOT_MCP_DATABASE", "").strip()
    if not value:
        raise RuntimeError("Governed MCP database is not configured")
    database = Path(value).resolve()
    if not database.is_file():
        raise RuntimeError("Governed MCP database is unavailable")
    return database


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
            rows.extend(dict(zip(columns, values, strict=True)) for values in cursor.fetchall())
    finally:
        connection.close()
    return rows


mcp = FastMCP(
    "Project Copilot governed HVAC evidence",
    instructions=(
        "Read-only fixed operations over one private synthetic DuckDB snapshot. "
        "No SQL text or filesystem path is accepted from the model."
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


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

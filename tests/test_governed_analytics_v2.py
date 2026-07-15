from pathlib import Path

import pytest

from project_copilot.analytics import AnalyticsWorkspace
from project_copilot.semantic_analytics import (
    GovernedAnalyticsError,
    GovernedAnalyticsTool,
)


CSV = """timestamp,supply_temp_c,return_temp_c,power_kw,cooling_kw,load_pct
2026-07-01T08:00:00,7.0,12.0,100.0,400.0,55.0
2026-07-01T09:00:00,7.2,12.7,110.0,462.0,62.0
2026-07-01T10:00:00,7.5,13.5,120.0,540.0,70.0
"""


def build_tool(tmp_path: Path) -> GovernedAnalyticsTool:
    csv_path = tmp_path / "telemetry.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    workspace = AnalyticsWorkspace.build(
        csv_path=csv_path,
        database_path=tmp_path / "analytics.duckdb",
    )
    return GovernedAnalyticsTool(workspace)


def test_semantic_tool_runs_allowlisted_peak_and_latest_operations(
    tmp_path: Path,
) -> None:
    tool = build_tool(tmp_path)

    peak = tool.run("peak_load")
    latest = tool.run("latest_reading")

    assert peak.rows[0]["peak_load_pct"] == 70.0
    assert "70.0%" in peak.summary
    assert latest.rows[0]["load_pct"] == 70.0
    assert latest.operation == "latest_reading"


def test_semantic_tool_rejects_unknown_operation_and_raw_sql(tmp_path: Path) -> None:
    tool = build_tool(tmp_path)

    with pytest.raises(GovernedAnalyticsError, match="allowlisted"):
        tool.run("SELECT * FROM telemetry")


@pytest.mark.parametrize(
    ("operation", "expected_key"),
    [
        ("efficiency_summary", "average_cop"),
        ("power_summary", "average_power_kw"),
        ("temperature_delta_summary", "average_delta_t_c"),
    ],
)
def test_semantic_tool_supports_broader_safe_summary_catalog(
    tmp_path: Path, operation: str, expected_key: str
) -> None:
    result = build_tool(tmp_path).run(operation)

    assert expected_key in result.rows[0]
    assert result.sql.lstrip().upper().startswith("SELECT")

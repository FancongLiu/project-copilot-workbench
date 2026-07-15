from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from project_copilot.analytics import AnalyticsWorkspace


class GovernedAnalyticsError(ValueError):
    """Raised when a model requests an operation outside the semantic catalog."""


@dataclass(frozen=True)
class GovernedAnalyticsResult:
    operation: str
    title: str
    summary: str
    sql: str
    rows: list[dict[str, Any]]
    chart_type: str


class GovernedAnalyticsTool:
    OPERATIONS = {
        "peak_load",
        "latest_reading",
        "efficiency_summary",
        "power_summary",
        "temperature_delta_summary",
    }

    def __init__(self, workspace: AnalyticsWorkspace) -> None:
        self.workspace = workspace

    def run(self, operation: str) -> GovernedAnalyticsResult:
        if operation not in self.OPERATIONS:
            raise GovernedAnalyticsError(
                f"Analytics operation is not allowlisted: {operation}"
            )
        if operation == "peak_load":
            return self._peak_load()
        if operation == "latest_reading":
            return self._latest_reading()
        return self._summary(operation)

    def _peak_load(self) -> GovernedAnalyticsResult:
        sql = """
            SELECT
                CAST(timestamp AS VARCHAR) AS timestamp,
                load_pct AS peak_load_pct
            FROM telemetry
            ORDER BY load_pct DESC, timestamp
            LIMIT 1
        """
        rows = self.workspace.query(sql)
        peak = rows[0]
        return GovernedAnalyticsResult(
            operation="peak_load",
            title="Peak load",
            summary=f"Peak load was {peak['peak_load_pct']:.1f}% at {peak['timestamp']}.",
            sql=sql.strip(),
            rows=rows,
            chart_type="metric",
        )

    def _latest_reading(self) -> GovernedAnalyticsResult:
        sql = """
            SELECT
                CAST(timestamp AS VARCHAR) AS timestamp,
                supply_temp_c,
                return_temp_c,
                power_kw,
                cooling_kw,
                load_pct
            FROM telemetry
            ORDER BY timestamp DESC
            LIMIT 1
        """
        rows = self.workspace.query(sql)
        latest = rows[0]
        return GovernedAnalyticsResult(
            operation="latest_reading",
            title="Latest validated reading",
            summary=(
                f"Latest load was {latest['load_pct']:.1f}% at {latest['timestamp']} "
                f"with {latest['power_kw']:.1f} kW power."
            ),
            sql=sql.strip(),
            rows=rows,
            chart_type="metric",
        )

    def _summary(self, operation: str) -> GovernedAnalyticsResult:
        definitions = {
            "efficiency_summary": (
                "Efficiency summary",
                """
                SELECT
                    AVG(cooling_kw / NULLIF(power_kw, 0)) AS average_cop,
                    MAX(cooling_kw / NULLIF(power_kw, 0)) AS maximum_cop,
                    COUNT(timestamp) AS reading_count
                FROM telemetry
                """,
                "average_cop",
                "Average COP",
            ),
            "power_summary": (
                "Power summary",
                """
                SELECT
                    AVG(power_kw) AS average_power_kw,
                    MAX(power_kw) AS maximum_power_kw,
                    COUNT(timestamp) AS reading_count
                FROM telemetry
                """,
                "average_power_kw",
                "Average power",
            ),
            "temperature_delta_summary": (
                "Temperature delta summary",
                """
                SELECT
                    AVG(return_temp_c - supply_temp_c) AS average_delta_t_c,
                    MAX(return_temp_c - supply_temp_c) AS maximum_delta_t_c,
                    COUNT(timestamp) AS reading_count
                FROM telemetry
                """,
                "average_delta_t_c",
                "Average delta T",
            ),
        }
        title, sql, value_key, label = definitions[operation]
        rows = self.workspace.query(sql)
        return GovernedAnalyticsResult(
            operation=operation,
            title=title,
            summary=f"{label} was {float(rows[0][value_key]):.2f} across validated telemetry.",
            sql=sql.strip(),
            rows=rows,
            chart_type="metric",
        )

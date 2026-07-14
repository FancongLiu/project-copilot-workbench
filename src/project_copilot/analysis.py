from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from project_copilot.analytics import AnalyticsWorkspace


class AnalysisIntentError(ValueError):
    """Raised when a request does not match an approved analysis intent."""


@dataclass(frozen=True)
class AnalysisResult:
    intent: str
    title: str
    summary: str
    sql: str
    rows: list[dict[str, Any]]
    chart_type: str


class ApprovedAnalysisEngine:
    def __init__(self, workspace: AnalyticsWorkspace) -> None:
        self.workspace = workspace

    def analyze(self, question: str) -> AnalysisResult:
        normalized = question.casefold().strip()
        if ("负荷" in normalized or "load" in normalized) and any(
            token in normalized for token in ("最高", "峰值", "peak", "max")
        ):
            return self._peak_load()
        if any(token in normalized for token in ("cop", "能效", "效率")):
            return self._cop_trend()
        if any(token in normalized for token in ("功率", "耗电", "power")):
            return self._power_trend()
        if any(token in normalized for token in ("温差", "delta t", "delta-t")):
            return self._delta_t_trend()
        raise AnalysisIntentError(
            "The request does not match an approved analysis intent"
        )

    def _peak_load(self) -> AnalysisResult:
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
        return AnalysisResult(
            intent="peak-load",
            title="Peak load",
            summary=f"Peak load was {peak['peak_load_pct']:.1f}% at {peak['timestamp']}.",
            sql=sql.strip(),
            rows=rows,
            chart_type="metric",
        )

    def _cop_trend(self) -> AnalysisResult:
        sql = """
            SELECT
                CAST(timestamp AS VARCHAR) AS timestamp,
                cooling_kw / NULLIF(power_kw, 0) AS cop
            FROM telemetry
            ORDER BY timestamp
        """
        rows = self.workspace.query(sql)
        average = sum(float(row["cop"]) for row in rows) / len(rows)
        return AnalysisResult(
            intent="cop-trend",
            title="COP trend",
            summary=f"Average COP across the selected period was {average:.2f}.",
            sql=sql.strip(),
            rows=rows,
            chart_type="line",
        )

    def _power_trend(self) -> AnalysisResult:
        sql = """
            SELECT CAST(timestamp AS VARCHAR) AS timestamp, power_kw
            FROM telemetry
            ORDER BY timestamp
        """
        rows = self.workspace.query(sql)
        return AnalysisResult(
            intent="power-trend",
            title="Power trend",
            summary=f"Power ranged across {len(rows)} validated telemetry points.",
            sql=sql.strip(),
            rows=rows,
            chart_type="line",
        )

    def _delta_t_trend(self) -> AnalysisResult:
        sql = """
            SELECT
                CAST(timestamp AS VARCHAR) AS timestamp,
                return_temp_c - supply_temp_c AS delta_t_c
            FROM telemetry
            ORDER BY timestamp
        """
        rows = self.workspace.query(sql)
        return AnalysisResult(
            intent="delta-t-trend",
            title="Temperature delta",
            summary=f"Temperature delta was calculated for {len(rows)} validated points.",
            sql=sql.strip(),
            rows=rows,
            chart_type="line",
        )

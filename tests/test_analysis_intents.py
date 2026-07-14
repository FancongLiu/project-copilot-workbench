from pathlib import Path

import pytest

from project_copilot.analysis import AnalysisIntentError, ApprovedAnalysisEngine
from project_copilot.analytics import AnalyticsWorkspace


CSV = """timestamp,supply_temp_c,return_temp_c,power_kw,cooling_kw,load_pct
2026-07-01T08:00:00,7.0,12.0,100.0,400.0,55.0
2026-07-01T09:00:00,7.2,12.7,110.0,462.0,62.0
2026-07-01T10:00:00,7.5,13.5,120.0,540.0,70.0
"""


def build_engine(tmp_path: Path) -> ApprovedAnalysisEngine:
    csv_path = tmp_path / "telemetry.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    workspace = AnalyticsWorkspace.build(
        csv_path=csv_path,
        database_path=tmp_path / "analytics.duckdb",
    )
    return ApprovedAnalysisEngine(workspace)


def test_analysis_engine_maps_natural_language_to_approved_peak_load_metric(
    tmp_path: Path,
) -> None:
    result = build_engine(tmp_path).analyze("哪个时刻的负荷最高？")

    assert result.intent == "peak-load"
    assert result.rows == [
        {
            "timestamp": "2026-07-01 10:00:00",
            "peak_load_pct": 70.0,
        }
    ]
    assert "70.0%" in result.summary


def test_analysis_engine_returns_efficiency_trend_for_cop_question(
    tmp_path: Path,
) -> None:
    result = build_engine(tmp_path).analyze("请查看 COP 效率趋势")

    assert result.intent == "cop-trend"
    assert len(result.rows) == 3
    assert result.rows[0]["cop"] == pytest.approx(4.0)


def test_analysis_engine_rejects_unapproved_free_form_requests(tmp_path: Path) -> None:
    with pytest.raises(AnalysisIntentError, match="approved analysis"):
        build_engine(tmp_path).analyze("写一段 Python 自动修改原始数据")

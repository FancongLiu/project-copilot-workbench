from pathlib import Path

import duckdb
import polars as pl
import pytest

from project_copilot.analytics import AnalyticsValidationError, AnalyticsWorkspace


VALID_CSV = """timestamp,supply_temp_c,return_temp_c,power_kw,cooling_kw,load_pct
2026-07-01T08:00:00,7.0,12.0,100.0,400.0,55.0
2026-07-01T09:00:00,7.2,12.7,110.0,462.0,62.0
2026-07-01T10:00:00,7.5,13.5,120.0,540.0,70.0
"""


def write_csv(path: Path, content: str = VALID_CSV) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_analytics_workspace_builds_read_only_snapshot_and_metrics(
    tmp_path: Path,
) -> None:
    csv_path = write_csv(tmp_path / "telemetry.csv")
    workspace = AnalyticsWorkspace.build(
        csv_path=csv_path,
        database_path=tmp_path / "analytics.duckdb",
    )

    metrics = workspace.metric_snapshot()

    assert metrics.row_count == 3
    assert metrics.average_power_kw == pytest.approx(110.0)
    assert metrics.average_delta_t_c == pytest.approx(5.5)
    assert metrics.average_cop == pytest.approx(4.233333, rel=1e-5)

    rows = workspace.query("select max(load_pct) as peak_load from telemetry")
    assert rows == [{"peak_load": 70.0}]


def test_analytics_workspace_rejects_invalid_hvac_ranges(tmp_path: Path) -> None:
    csv_path = write_csv(
        tmp_path / "invalid.csv",
        VALID_CSV.replace("70.0\n", "140.0\n"),
    )

    with pytest.raises(AnalyticsValidationError):
        AnalyticsWorkspace.build(
            csv_path=csv_path,
            database_path=tmp_path / "analytics.duckdb",
        )


def test_analytics_workspace_rejects_return_temperature_below_supply(
    tmp_path: Path,
) -> None:
    csv_path = write_csv(
        tmp_path / "invalid_delta.csv",
        VALID_CSV.replace("7.5,13.5", "14.0,13.5"),
    )

    with pytest.raises(AnalyticsValidationError, match="return temperature"):
        AnalyticsWorkspace.build(
            csv_path=csv_path,
            database_path=tmp_path / "analytics.duckdb",
        )


def test_failed_snapshot_build_preserves_previous_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    csv_path = write_csv(tmp_path / "telemetry.csv")
    database_path = tmp_path / "analytics.duckdb"
    AnalyticsWorkspace.build(csv_path=csv_path, database_path=database_path)

    def fail_write_csv(self, file, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("simulated disk failure")

    monkeypatch.setattr(pl.DataFrame, "write_csv", fail_write_csv)

    with pytest.raises(AnalyticsValidationError, match="snapshot build failed"):
        AnalyticsWorkspace.build(csv_path=csv_path, database_path=database_path)

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        assert connection.execute("select count(*) from telemetry").fetchone() == (3,)
    finally:
        connection.close()
    assert list(tmp_path.glob("*.curated.csv")) == []
    assert list(tmp_path.glob("*.building.duckdb")) == []

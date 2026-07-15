from pathlib import Path

import duckdb
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


@pytest.mark.parametrize(
    "content, message",
    [
        (
            "timestamp,supply_temp_c,return_temp_c,power_kw,cooling_kw,load_pct\n",
            "at least one row",
        ),
        (VALID_CSV.replace("100.0,400.0", "inf,400.0"), "finite"),
    ],
)
def test_analytics_workspace_rejects_empty_or_non_finite_telemetry(
    tmp_path: Path, content: str, message: str
) -> None:
    csv_path = write_csv(tmp_path / "invalid.csv", content)

    with pytest.raises(AnalyticsValidationError, match=message):
        AnalyticsWorkspace.build(
            csv_path=csv_path,
            database_path=tmp_path / "analytics.duckdb",
        )


def test_failed_snapshot_build_preserves_previous_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    csv_path = write_csv(tmp_path / "telemetry.csv")
    previous_database = tmp_path / "sha-old.duckdb"
    failed_database = tmp_path / "sha-new.duckdb"
    AnalyticsWorkspace.build(csv_path=csv_path, database_path=previous_database)

    def fail_population(connection, frame, curated_path):  # type: ignore[no-untyped-def]
        del connection, frame, curated_path
        raise OSError("simulated disk failure")

    monkeypatch.setattr(AnalyticsWorkspace, "_populate_database", fail_population)

    with pytest.raises(AnalyticsValidationError, match="snapshot build failed"):
        AnalyticsWorkspace.build(csv_path=csv_path, database_path=failed_database)

    connection = duckdb.connect(str(previous_database), read_only=True)
    try:
        assert connection.execute("select count(*) from telemetry").fetchone() == (3,)
    finally:
        connection.close()
    assert list(tmp_path.glob("*.curated.csv")) == []
    assert list(tmp_path.glob("*.building.duckdb")) == []
    assert not failed_database.exists()


def test_existing_content_addressed_snapshot_is_reused_with_reader_open(
    tmp_path: Path,
) -> None:
    csv_path = write_csv(tmp_path / "telemetry.csv")
    database_path = tmp_path / "sha256-immutable.duckdb"
    first = AnalyticsWorkspace.build(csv_path=csv_path, database_path=database_path)
    reader = first._connect_read_only()
    try:
        second = AnalyticsWorkspace.build(
            csv_path=csv_path,
            database_path=database_path,
        )
        assert second.metric_snapshot().row_count == 3
        assert reader.execute("select count(*) from telemetry").fetchone() == (3,)
    finally:
        reader.close()


def test_existing_snapshot_does_not_bypass_source_validation(tmp_path: Path) -> None:
    csv_path = write_csv(tmp_path / "telemetry.csv")
    database_path = tmp_path / "sha256-immutable.duckdb"
    AnalyticsWorkspace.build(csv_path=csv_path, database_path=database_path)
    csv_path.write_text(
        "timestamp,supply_temp_c,return_temp_c,power_kw,cooling_kw,load_pct\n",
        encoding="utf-8",
    )

    with pytest.raises(AnalyticsValidationError, match="at least one row"):
        AnalyticsWorkspace.build(csv_path=csv_path, database_path=database_path)

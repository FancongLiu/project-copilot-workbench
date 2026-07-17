from __future__ import annotations

from pathlib import Path
from shutil import copy2

import duckdb
import pytest

from project_copilot.hvac_snapshot import (
    HVACSnapshotInspector,
    SnapshotInspectionError,
)


DATABASE = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "agentic_hvac_bakeoff"
    / "datasets"
    / "hvac_bakeoff.duckdb"
)
TIMEZONE = "Asia/Shanghai"


def _inspector(database: Path = DATABASE) -> HVACSnapshotInspector:
    return HVACSnapshotInspector(database, timezone_name=TIMEZONE)


def _row(
    rows: list[dict[str, object]], event_type: str, asset_id: str
) -> dict[str, object]:
    return next(
        row
        for row in rows
        if row["event_type"] == event_type and row["asset_id"] == asset_id
    )


def test_data_quality_inspection_finds_snapshot_wide_defects() -> None:
    result = _inspector().inspect("data_quality")

    hp02_coverage = _row(result.rows, "coverage", "HP-02")
    duplicate = _row(result.rows, "duplicate_timestamp", "HP-03")
    out_of_order = _row(result.rows, "out_of_order", "HP-04")
    frozen = _row(result.rows, "frozen_sensor_tuple", "HP-02")

    assert hp02_coverage["missing_samples"] == 60
    assert hp02_coverage["completeness_pct"] == pytest.approx(99.768519)
    assert duplicate["event_count"] == 30
    assert out_of_order["event_count"] == 59
    assert frozen["sample_count"] == 90
    assert frozen["duration_seconds"] == 900


def test_control_event_inspection_segments_common_hvac_events() -> None:
    result = _inspector().inspect("control_events")

    compressor = _row(result.rows, "compressor_feedback_mismatch", "HP-02")
    compressor_observation = _row(
        result.rows,
        "compressor_feedback_mismatch_observation",
        "HP-01",
    )
    expansion_valve = _row(result.rows, "eev_feedback_mismatch", "HP-03")
    short_cycling = _row(result.rows, "short_cycling", "HP-04")
    flow_loss = _row(result.rows, "flow_proof_loss", "HP-01")
    fan = _row(result.rows, "outdoor_fan_feedback_mismatch", "HP-04")
    defrost = _row(result.rows, "defrost", "HP-01")

    assert compressor["duration_seconds"] == 420
    assert compressor["max_deviation"] == 50
    assert compressor_observation["duration_seconds"] == 10
    assert expansion_valve["average_deviation"] == pytest.approx(30)
    assert short_cycling["start_count"] == 6
    assert short_cycling["threshold_start_count"] == 4
    assert short_cycling["threshold_exceedance_pct"] == 50.0
    assert flow_loss["start_time"] == "2026-01-15T10:20:00+08:00"
    assert fan["duration_seconds"] == 900
    assert fan["alarm_code"] == "A311"
    assert defrost["duration_seconds"] == 480
    assert defrost["end_time"] == "2026-01-16T18:38:00+08:00"
    assert defrost["min_compressor_fb_hz"] == 55
    assert defrost["max_outdoor_fan_fb_pct"] == 0
    assert defrost["outdoor_coil_temp_rise_c"] == pytest.approx(14.0)
    assert flow_loss["min_compressor_cmd_hz"] > 0
    assert flow_loss["min_compressor_fb_hz"] == 0


def test_alarm_inspection_links_alarm_code_and_observed_values() -> None:
    result = _inspector().inspect("alarm_events")

    discharge = next(row for row in result.rows if row["alarm_code"] == "A217")
    fan = next(row for row in result.rows if row["alarm_code"] == "A311")

    assert discharge["asset_id"] == "HP-03"
    assert discharge["duration_seconds"] == 1200
    assert discharge["max_discharge_temp_c"] == 130
    assert fan["asset_id"] == "HP-04"
    assert fan["average_outdoor_fan_cmd_pct"] == 80
    assert fan["average_outdoor_fan_fb_pct"] == 0


def test_metric_extreme_finds_sustained_low_suction_window() -> None:
    result = _inspector().metric_extreme(
        "suction_pressure_kpa_g",
        "minimum",
    )

    assert result.rows == [
        {
            "event_type": "metric_minimum",
            "asset_id": "HP-04",
            "metric": "suction_pressure_kpa_g",
            "unit": "kPa(g)",
            "extreme_value": 320.0,
            "start_time": "2026-01-17T06:00:00+08:00",
            "end_time": "2026-01-17T07:00:00+08:00",
            "sample_count": 360,
            "duration_seconds": 3600,
            "average_thermal_output_kw": 31.5,
            "average_cop": 2.25,
            "average_superheat_k": 18.0,
        }
    ]


def test_metric_extreme_rejects_unknown_metric() -> None:
    with pytest.raises(SnapshotInspectionError, match="metric is not allowlisted"):
        _inspector().metric_extreme("password", "minimum")


def test_metric_extreme_splits_disjoint_minimum_windows() -> None:
    rows = (
        _inspector()
        .metric_extreme(
            "cop",
            "minimum",
            "HP-04",
        )
        .rows
    )

    assert len(rows) == 6
    assert {row["sample_count"] for row in rows} == {30}
    assert {row["duration_seconds"] for row in rows} == {300}
    assert rows[0]["start_time"] == "2026-01-16T00:00:00+08:00"
    assert rows[-1]["end_time"] == "2026-01-16T00:55:00+08:00"


def test_control_and_alarm_inspection_split_disjoint_windows(tmp_path: Path) -> None:
    database = tmp_path / "disjoint.duckdb"
    connection = duckdb.connect(str(database))
    try:
        connection.execute(
            """
            CREATE TABLE telemetry_clean (
                timestamp TIMESTAMPTZ,
                asset_id VARCHAR,
                compressor_cmd_hz DOUBLE,
                compressor_fb_hz DOUBLE,
                eev_cmd_pct DOUBLE,
                eev_fb_pct DOUBLE,
                outdoor_fan_cmd_pct DOUBLE,
                outdoor_fan_fb_pct DOUBLE,
                indoor_fan_cmd_pct DOUBLE,
                indoor_fan_fb_pct DOUBLE,
                alarm_active BIGINT,
                alarm_code VARCHAR,
                discharge_temp_c DOUBLE,
                outdoor_coil_temp_c DOUBLE,
                enable_cmd BIGINT,
                flow_proof BIGINT,
                defrost_cmd BIGINT
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO telemetry_clean VALUES (
                ?, 'HP-X', 50, 0, 50, 50, 50, 50, 50, 50,
                1, 'A999', 90, 5, 1, 1, 0
            )
            """,
            [
                ["2026-01-15T00:00:00+08:00"],
                ["2026-01-15T00:00:10+08:00"],
                ["2026-01-15T00:10:00+08:00"],
                ["2026-01-15T00:10:10+08:00"],
            ],
        )
    finally:
        connection.close()

    inspector = _inspector(database)
    compressor = [
        row
        for row in inspector.inspect("control_events").rows
        if row["event_type"] == "compressor_feedback_mismatch_observation"
    ]
    alarms = inspector.inspect("alarm_events").rows

    assert len(compressor) == 2
    assert [row["duration_seconds"] for row in compressor] == [20, 20]
    assert len(alarms) == 2
    assert [row["duration_seconds"] for row in alarms] == [20, 20]


def test_short_cycling_does_not_count_an_active_hour_boundary_as_a_start(
    tmp_path: Path,
) -> None:
    database = tmp_path / "hour-boundary.duckdb"
    copy2(DATABASE, database)
    connection = duckdb.connect(str(database))
    try:
        rows = [
            ("2026-01-15T00:59:50+08:00", 1),
            ("2026-01-15T01:00:00+08:00", 1),
            ("2026-01-15T01:05:00+08:00", 0),
            ("2026-01-15T01:05:10+08:00", 1),
            ("2026-01-15T01:10:00+08:00", 0),
            ("2026-01-15T01:10:10+08:00", 1),
            ("2026-01-15T01:15:00+08:00", 0),
            ("2026-01-15T01:15:10+08:00", 1),
            ("2026-01-15T01:20:00+08:00", 0),
            ("2026-01-15T01:20:10+08:00", 1),
        ]
        for ingest_seq, (timestamp, enable_cmd) in enumerate(rows, start=2_000_000):
            connection.execute(
                """
                INSERT INTO telemetry_raw
                SELECT * REPLACE (
                    ? AS ingest_seq,
                    ?::TIMESTAMPTZ AS timestamp,
                    'HP-X' AS asset_id,
                    ? AS enable_cmd
                )
                FROM telemetry_raw
                LIMIT 1
                """,
                [ingest_seq, timestamp, enable_cmd],
            )
    finally:
        connection.close()

    short_cycling = [
        row
        for row in _inspector(database).inspect("control_events").rows
        if row["event_type"] == "short_cycling" and row["asset_id"] == "HP-X"
    ]

    assert short_cycling == []


def test_snapshot_inspection_rejects_unknown_operation() -> None:
    with pytest.raises(SnapshotInspectionError, match="allowlisted"):
        _inspector().inspect("raw_sql")


def test_snapshot_inspector_requires_an_explicit_project_timezone() -> None:
    with pytest.raises(TypeError, match="timezone_name"):
        HVACSnapshotInspector(DATABASE)

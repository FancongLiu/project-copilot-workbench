from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = (
    REPOSITORY_ROOT
    / "examples"
    / "agentic_hvac_bakeoff"
    / "datasets"
    / "hvac_bakeoff.duckdb"
)
DEFAULT_OUTPUT = REPOSITORY_ROOT / "evaluation" / "results" / "agentic-hvac-gold.json"


def _rounded(value: Any, digits: int = 6) -> float:
    return round(float(value), digits)


def _one(connection: duckdb.DuckDBPyConnection, sql: str) -> tuple[Any, ...]:
    row = connection.execute(sql).fetchone()
    if row is None:
        raise RuntimeError("Gold query returned no row")
    return row


def _short_cycle_metrics(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, Any]:
    values = [
        float(row[0])
        for row in connection.execute(
            """
            SELECT compressor_fb_hz
            FROM telemetry_clean
            WHERE asset_id = 'HP-04'
              AND timestamp >= '2026-01-16T00:00:00+08:00'
              AND timestamp < '2026-01-16T01:00:00+08:00'
            ORDER BY timestamp
            """
        ).fetchall()
    ]
    states = [value > 0 for value in values]
    starts = int(bool(states and states[0])) + sum(
        current and not previous for previous, current in zip(states, states[1:])
    )
    runs: list[tuple[bool, int]] = []
    for state in states:
        if not runs or runs[-1][0] != state:
            runs.append((state, 1))
        else:
            runs[-1] = (state, runs[-1][1] + 1)
    return {
        "asset_id": "HP-04",
        "starts_in_hour": starts,
        "shortest_on_seconds": min(count for state, count in runs if state) * 10,
        "shortest_off_seconds": min(count for state, count in runs if not state) * 10,
    }


def compute_gold_metrics(
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    database = Path(database_path).resolve()
    connection = duckdb.connect(str(database), read_only=True)
    try:
        raw_count = int(_one(connection, "SELECT count(*) FROM telemetry_raw")[0])
        unique_count = int(_one(connection, "SELECT count(*) FROM telemetry_clean")[0])
        duplicate_keys, redundant_rows = _one(
            connection,
            """
            SELECT count(*), sum(row_count - 1)
            FROM (
                SELECT asset_id, timestamp, count(*) AS row_count
                FROM telemetry_raw
                GROUP BY asset_id, timestamp
                HAVING count(*) > 1
            )
            """,
        )
        negative_steps = int(
            _one(
                connection,
                """
                SELECT count(*)
                FROM (
                    SELECT timestamp,
                           lag(timestamp) OVER (ORDER BY ingest_seq) AS previous_timestamp
                    FROM telemetry_raw
                    WHERE asset_id = 'HP-04'
                      AND timestamp >= '2026-01-15T12:00:00+08:00'
                      AND timestamp < '2026-01-15T12:10:00+08:00'
                )
                WHERE timestamp < previous_timestamp
                """,
            )[0]
        )
        mismatch_count, mismatch_error = _one(
            connection,
            """
            SELECT count(*), avg(abs(compressor_cmd_hz - compressor_fb_hz))
            FROM telemetry_clean
            WHERE asset_id = 'HP-02'
              AND timestamp >= '2026-01-15T18:00:00+08:00'
              AND timestamp < '2026-01-15T18:07:00+08:00'
            """,
        )
        high_count, high_max = _one(
            connection,
            """
            SELECT count(*), max(discharge_temp_c)
            FROM telemetry_clean
            WHERE asset_id = 'HP-03'
              AND timestamp >= '2026-01-15T20:00:00+08:00'
              AND timestamp < '2026-01-15T20:20:00+08:00'
            """,
        )
        short_cycling = _short_cycle_metrics(connection)
        efficiency = _one(
            connection,
            """
            SELECT sum(electric_power_kw * 10 / 3600),
                   sum(thermal_output_kw * 10 / 3600),
                   sum(thermal_output_kw * 10 / 3600)
                     / sum(electric_power_kw * 10 / 3600)
            FROM telemetry_clean
            WHERE asset_id = 'HP-01'
              AND timestamp >= '2026-01-16T04:00:00+08:00'
              AND timestamp < '2026-01-16T05:00:00+08:00'
            """,
        )
        before = _one(
            connection,
            """
            SELECT avg(supply_air_temp_c), sum(electric_power_kw * 10 / 3600)
            FROM telemetry_clean
            WHERE asset_id = 'HP-02'
              AND timestamp >= '2026-01-16T10:00:00+08:00'
              AND timestamp < '2026-01-16T12:00:00+08:00'
            """,
        )
        after = _one(
            connection,
            """
            SELECT avg(supply_air_temp_c), sum(electric_power_kw * 10 / 3600)
            FROM telemetry_clean
            WHERE asset_id = 'HP-02'
              AND timestamp >= '2026-01-16T12:00:00+08:00'
              AND timestamp < '2026-01-16T14:00:00+08:00'
            """,
        )
        defrost = _one(
            connection,
            """
            SELECT count(*),
                   count(*) FILTER (WHERE compressor_fb_hz > 0),
                   count(*) FILTER (WHERE outdoor_fan_fb_pct = 0),
                   max(outdoor_coil_temp_c) - min(outdoor_coil_temp_c)
            FROM telemetry_clean
            WHERE asset_id = 'HP-01'
              AND timestamp >= '2026-01-16T18:30:00+08:00'
              AND timestamp < '2026-01-16T18:38:00+08:00'
            """,
        )
        valve = _one(
            connection,
            """
            SELECT count(*), count(DISTINCT eev_fb_pct),
                   avg(abs(eev_cmd_pct - eev_fb_pct))
            FROM telemetry_clean
            WHERE asset_id = 'HP-03'
              AND timestamp >= '2026-01-17T02:00:00+08:00'
              AND timestamp < '2026-01-17T02:30:00+08:00'
            """,
        )
        low_suction = _one(
            connection,
            """
            SELECT count(*), avg(suction_pressure_kpa_g),
                   avg(thermal_output_kw), avg(cop)
            FROM telemetry_clean
            WHERE asset_id = 'HP-04'
              AND timestamp >= '2026-01-17T06:00:00+08:00'
              AND timestamp < '2026-01-17T07:00:00+08:00'
            """,
        )
        frozen_count = int(
            _one(
                connection,
                """
                SELECT count(*)
                FROM telemetry_clean
                WHERE asset_id = 'HP-02'
                  AND timestamp >= '2026-01-17T08:00:00+08:00'
                  AND timestamp < '2026-01-17T08:15:00+08:00'
                """,
            )[0]
        )
        frozen_distinct = int(
            _one(
                connection,
                """
                SELECT count(*)
                FROM (
                    SELECT DISTINCT ambient_temp_c, return_air_temp_c,
                           supply_air_temp_c, suction_temp_c, discharge_temp_c,
                           liquid_temp_c, suction_pressure_kpa_g,
                           discharge_pressure_kpa_g, compressor_fb_hz
                    FROM telemetry_clean
                    WHERE asset_id = 'HP-02'
                      AND timestamp >= '2026-01-17T08:00:00+08:00'
                      AND timestamp < '2026-01-17T08:15:00+08:00'
                )
                """,
            )[0]
        )
    finally:
        connection.close()

    return {
        "row_counts": {"raw": raw_count, "unique": unique_count, "ideal": 103_680},
        "data_gap": {
            "asset_id": "HP-02",
            "missing_points": 103_680 // 4 - (unique_count - 3 * (103_680 // 4)),
            "duration_seconds": 600,
        },
        "duplicates": {
            "asset_id": "HP-03",
            "duplicate_keys": int(duplicate_keys),
            "redundant_rows": int(redundant_rows),
        },
        "out_of_order": {
            "asset_id": "HP-04",
            "negative_time_steps": negative_steps,
        },
        "command_feedback_mismatch": {
            "asset_id": "HP-02",
            "sample_count": int(mismatch_count),
            "duration_seconds": int(mismatch_count) * 10,
            "mean_absolute_error_hz": _rounded(mismatch_error),
        },
        "high_discharge_temperature": {
            "asset_id": "HP-03",
            "sample_count": int(high_count),
            "duration_seconds": int(high_count) * 10,
            "maximum_c": _rounded(high_max),
        },
        "short_cycling": short_cycling,
        "efficiency_degradation": {
            "asset_id": "HP-01",
            "electric_energy_kwh": _rounded(efficiency[0]),
            "thermal_energy_kwh": _rounded(efficiency[1]),
            "weighted_cop": _rounded(efficiency[2]),
        },
        "configuration_change": {
            "asset_id": "HP-02",
            "before_supply_air_mean_c": _rounded(before[0]),
            "after_supply_air_mean_c": _rounded(after[0]),
            "difference_c": _rounded(float(after[0]) - float(before[0])),
            "before_energy_kwh": _rounded(before[1]),
            "after_energy_kwh": _rounded(after[1]),
        },
        "defrost_sequence": {
            "asset_id": "HP-01",
            "sample_count": int(defrost[0]),
            "duration_seconds": int(defrost[0]) * 10,
            "compressor_on_samples": int(defrost[1]),
            "outdoor_fan_off_samples": int(defrost[2]),
            "coil_temperature_rise_c": _rounded(defrost[3]),
        },
        "valve_stuck": {
            "asset_id": "HP-03",
            "sample_count": int(valve[0]),
            "feedback_distinct_values": int(valve[1]),
            "mean_absolute_error_pct_points": _rounded(valve[2]),
        },
        "low_suction_pressure": {
            "asset_id": "HP-04",
            "sample_count": int(low_suction[0]),
            "suction_pressure_kpa_g": _rounded(low_suction[1]),
            "thermal_output_kw": _rounded(low_suction[2]),
            "cop": _rounded(low_suction[3]),
        },
        "telemetry_freeze": {
            "asset_id": "HP-02",
            "sample_count": frozen_count,
            "duration_seconds": frozen_count * 10,
            "sensor_tuple_distinct_values": frozen_distinct,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recompute synthetic HVAC gold metrics"
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = compute_gold_metrics(args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote recomputed gold metrics to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

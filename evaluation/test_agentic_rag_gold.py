from __future__ import annotations

import importlib.util
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPOSITORY_ROOT / "evaluation" / "run_agentic_rag_bakeoff.py"
DATABASE_PATH = (
    REPOSITORY_ROOT
    / "examples"
    / "agentic_hvac_bakeoff"
    / "datasets"
    / "hvac_bakeoff.duckdb"
)


def _load_runner():
    assert RUNNER_PATH.is_file(), "Agentic RAG gold evaluator is missing"
    spec = importlib.util.spec_from_file_location("agentic_rag_gold", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gold_metrics_are_recomputed_from_the_structured_data() -> None:
    runner = _load_runner()
    metrics = runner.compute_gold_metrics(DATABASE_PATH)

    assert metrics["row_counts"] == {
        "raw": 103_650,
        "unique": 103_620,
        "ideal": 103_680,
    }
    assert metrics["data_gap"] == {
        "asset_id": "HP-02",
        "missing_points": 60,
        "duration_seconds": 600,
    }
    assert metrics["duplicates"] == {
        "asset_id": "HP-03",
        "duplicate_keys": 30,
        "redundant_rows": 30,
    }
    assert metrics["out_of_order"] == {
        "asset_id": "HP-04",
        "negative_time_steps": 59,
    }
    assert metrics["command_feedback_mismatch"]["sample_count"] == 42
    assert metrics["command_feedback_mismatch"]["duration_seconds"] == 420
    assert metrics["command_feedback_mismatch"]["mean_absolute_error_hz"] == 50.0
    assert metrics["high_discharge_temperature"] == {
        "asset_id": "HP-03",
        "sample_count": 120,
        "duration_seconds": 1200,
        "maximum_c": 130.0,
    }
    assert metrics["short_cycling"] == {
        "asset_id": "HP-04",
        "starts_in_hour": 6,
        "shortest_on_seconds": 300,
        "shortest_off_seconds": 300,
    }
    assert metrics["efficiency_degradation"] == {
        "asset_id": "HP-01",
        "electric_energy_kwh": 20.0,
        "thermal_energy_kwh": 40.0,
        "weighted_cop": 2.0,
    }
    assert metrics["configuration_change"] == {
        "asset_id": "HP-02",
        "before_supply_air_mean_c": 12.2,
        "after_supply_air_mean_c": 10.3,
        "difference_c": -1.9,
        "before_energy_kwh": 36.0,
        "after_energy_kwh": 40.0,
    }
    assert metrics["defrost_sequence"] == {
        "asset_id": "HP-01",
        "sample_count": 48,
        "duration_seconds": 480,
        "compressor_on_samples": 48,
        "outdoor_fan_off_samples": 48,
        "coil_temperature_rise_c": 14.0,
    }
    assert metrics["valve_stuck"] == {
        "asset_id": "HP-03",
        "sample_count": 180,
        "feedback_distinct_values": 1,
        "mean_absolute_error_pct_points": 30.0,
    }
    assert metrics["low_suction_pressure"] == {
        "asset_id": "HP-04",
        "sample_count": 360,
        "suction_pressure_kpa_g": 320.0,
        "thermal_output_kw": 31.5,
        "cop": 2.25,
    }
    assert metrics["telemetry_freeze"] == {
        "asset_id": "HP-02",
        "sample_count": 90,
        "duration_seconds": 900,
        "sensor_tuple_distinct_values": 1,
    }

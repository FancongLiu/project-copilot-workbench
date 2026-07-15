from datetime import datetime, timedelta
from pathlib import Path

from hypothesis import HealthCheck, given, settings, strategies as st
import pytest

from project_copilot.defrost_diagnostics import (
    DefrostAssetContext,
    DefrostDiagnosticsEngine,
    DefrostDiagnosticsError,
    DefrostRulePack,
)


RULES = {
    "schema_version": "1.0",
    "rule_id": "SYN-HP01-DEFROST",
    "version": "2026-07-15",
    "asset_id": "HP-01",
    "controller_model": "AuroraCTRL-700",
    "firmware_version": "SYN-3.4.2",
    "compliance_scope": "synthetic_demo",
    "timezone": "Asia/Shanghai",
    "source_file": "defrost-control-sequence.md",
    "source_section": "Synthetic sequence / Entry and exit",
    "sample_interval_seconds": 10,
    "required_resolution_seconds": 10,
    "gap_tolerance_seconds": 2,
    "candidate_outdoor_temp_c_max": 5.0,
    "candidate_coil_temp_c_max": 0.0,
    "candidate_min_seconds": 20,
    "initiation_max_delay_seconds": 60,
    "defrost_max_seconds": 120,
    "exit_coil_temp_c_min": 5.0,
    "recovery_min_seconds": 10,
    "defrost_fan_expected": 0,
    "defrost_reversing_valve_expected": 1,
}

CONTEXT = {
    "schema_version": "1.0",
    "asset_id": "HP-01",
    "controller_model": "AuroraCTRL-700",
    "firmware_version": "SYN-3.4.2",
    "source_file": "asset-register-heat-pump.md",
    "source_section": "HP-01 equipment identity",
}


HEADER = (
    "timestamp,asset_id,mode,outdoor_temp_c,outdoor_coil_temp_c,"
    "suction_pressure_kpa,discharge_pressure_kpa,suction_temp_c,"
    "discharge_temp_c,superheat_k,subcooling_k,compressor_command,"
    "outdoor_fan_command,reversing_valve_command,defrost_command,"
    "alarm_code,data_quality\n"
)


def write_csv(path: Path, rows: list[str]) -> None:
    path.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")


def engine_for(
    csv_path: Path,
    *,
    rules: dict[str, object] | None = None,
    context: dict[str, object] | None = None,
) -> DefrostDiagnosticsEngine:
    return DefrostDiagnosticsEngine(
        csv_path,
        DefrostRulePack.model_validate(rules or RULES),
        DefrostAssetContext.model_validate(context or CONTEXT),
    )


def test_compliant_defrost_replay_returns_state_and_rule_evidence(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "defrost.csv"
    write_csv(
        csv_path,
        [
            "2026-07-15T03:59:50,HP-01,heating,2,-2,420,1700,2,76,4,5,1,1,0,0,,good",
            "2026-07-15T04:00:00,HP-01,heating,2,-2,418,1702,2,77,4,5,1,1,0,0,,good",
            "2026-07-15T04:00:10,HP-01,heating,2,-2,416,1705,2,78,4,5,1,1,0,0,,good",
            "2026-07-15T04:00:20,HP-01,defrost,2,-1,430,1680,3,74,4,5,1,0,1,1,,good",
            "2026-07-15T04:00:30,HP-01,defrost,2,3,440,1650,4,70,4,5,1,0,1,1,,good",
            "2026-07-15T04:00:40,HP-01,recovery,2,6,435,1660,4,71,4,5,1,0,0,0,,good",
            "2026-07-15T04:00:50,HP-01,heating,2,1,425,1690,3,75,4,5,1,1,0,0,,good",
        ],
    )
    engine = engine_for(csv_path)

    result = engine.analyze(
        asset_id="HP-01",
        start="2026-07-15T03:59:50",
        end="2026-07-15T04:01:00",
    )

    assert result.status == "compliant"
    assert result.violation_count == 0
    assert result.sample_count == 7
    assert [transition.to_state for transition in result.transitions] == [
        "candidate",
        "defrost",
        "recovery",
        "heating",
    ]
    assert result.rule_source == "defrost-control-sequence.md"
    assert result.controller_model == "AuroraCTRL-700"
    assert result.firmware_version == "SYN-3.4.2"
    assert result.compliance_scope == "synthetic_demo"
    assert result.timestamp_uncertainty_seconds == 10
    assert "0 violation" in result.summary


def test_noncompliant_replay_reports_first_deviation_and_observed_fields(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "defrost.csv"
    write_csv(
        csv_path,
        [
            "2026-07-15T15:59:50,HP-01,heating,10,7,500,1600,8,70,4,5,1,1,0,0,,good",
            "2026-07-15T16:00:00,HP-01,defrost,10,7,505,1590,8,69,4,5,1,1,1,1,,good",
            "2026-07-15T16:00:10,HP-01,defrost,10,8,510,1580,9,68,4,5,1,1,1,1,,good",
            "2026-07-15T16:00:20,HP-01,recovery,10,8,505,1590,8,69,4,5,1,0,0,0,,good",
            "2026-07-15T16:00:30,HP-01,heating,10,7,500,1600,8,70,4,5,1,1,0,0,,good",
        ],
    )
    engine = engine_for(csv_path)

    result = engine.analyze(
        asset_id="HP-01",
        start="2026-07-15T15:59:50",
        end="2026-07-15T16:00:40",
    )

    assert result.status == "non_compliant"
    assert {item.code for item in result.violations} >= {
        "entry_without_candidate",
        "outdoor_fan_on_during_defrost",
    }
    assert result.first_deviation_at == "2026-07-15 16:00:00"
    assert result.violations[0].observed
    assert "non-compliant" in result.summary


def test_duplicate_or_gapped_telemetry_returns_insufficient_data(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "defrost.csv"
    duplicate = "2026-07-15T04:00:00,HP-01,heating,2,-2,420,1700,2,76,4,5,1,1,0,0,,good"
    write_csv(csv_path, [duplicate, duplicate])
    engine = engine_for(csv_path)

    result = engine.analyze(
        asset_id="HP-01",
        start="2026-07-15T03:59:50",
        end="2026-07-15T04:01:00",
    )

    assert result.status == "insufficient_data"
    assert result.violation_count == 0
    assert "duplicate timestamp" in result.summary


@given(
    candidate_steps=st.integers(min_value=2, max_value=5),
    defrost_steps=st.integers(min_value=1, max_value=12),
)
@settings(
    max_examples=12,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_all_approved_candidate_and_duration_boundaries_remain_compliant(
    tmp_path: Path, candidate_steps: int, defrost_steps: int
) -> None:
    base = datetime(2026, 7, 15, 4, 0)
    rows: list[str] = []
    for step in range(candidate_steps + 1):
        timestamp = (base + timedelta(seconds=step * 10)).isoformat()
        rows.append(f"{timestamp},HP-01,heating,2,-2,420,1700,2,76,4,5,1,1,0,0,,good")
    defrost_start = (candidate_steps + 1) * 10
    for step in range(defrost_steps):
        second = defrost_start + step * 10
        timestamp = (base + timedelta(seconds=second)).isoformat()
        rows.append(f"{timestamp},HP-01,defrost,2,3,430,1680,3,74,4,5,1,0,1,1,,good")
    exit_second = defrost_start + defrost_steps * 10
    exit_timestamp = (base + timedelta(seconds=exit_second)).isoformat()
    recovery_mid_timestamp = (base + timedelta(seconds=exit_second + 10)).isoformat()
    recovery_timestamp = (base + timedelta(seconds=exit_second + 20)).isoformat()
    window_end = (base + timedelta(seconds=exit_second + 30)).isoformat()
    rows.extend(
        [
            f"{exit_timestamp},HP-01,recovery,2,6,435,1660,4,71,4,5,1,0,0,0,,good",
            f"{recovery_mid_timestamp},HP-01,recovery,2,6,435,1660,4,71,4,5,1,0,0,0,,good",
            f"{recovery_timestamp},HP-01,heating,2,1,425,1690,3,75,4,5,1,1,0,0,,good",
        ]
    )
    csv_path = tmp_path / "property-defrost.csv"
    write_csv(csv_path, rows)

    result = engine_for(csv_path).analyze(
        asset_id="HP-01",
        start="2026-07-15T04:00:00",
        end=window_end,
    )

    assert result.status == "compliant"


def test_defrost_start_after_maximum_initiation_delay_is_noncompliant(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "late-defrost.csv"
    rows = [
        f"2026-07-15T04:00:{second:02d},HP-01,heating,2,-2,420,1700,2,76,4,5,1,1,0,0,,good"
        for second in range(0, 61, 10)
    ]
    rows.extend(
        [
            "2026-07-15T04:01:10,HP-01,defrost,2,3,430,1680,3,74,4,5,1,0,1,1,,good",
            "2026-07-15T04:01:20,HP-01,recovery,2,6,435,1660,4,71,4,5,1,0,0,0,,good",
            "2026-07-15T04:01:30,HP-01,heating,2,1,425,1690,3,75,4,5,1,1,0,0,,good",
        ]
    )
    write_csv(csv_path, rows)

    result = engine_for(csv_path).analyze(
        asset_id="HP-01",
        start="2026-07-15T04:00:00",
        end="2026-07-15T04:01:40",
    )

    assert result.status == "non_compliant"
    assert "defrost_started_after_max_delay" in {
        violation.code for violation in result.violations
    }


def test_rule_pack_must_match_asset_controller_and_firmware(tmp_path: Path) -> None:
    csv_path = tmp_path / "binding.csv"
    write_csv(
        csv_path,
        ["2026-07-15T04:00:00,HP-01,heating,10,7,500,1600,8,70,4,5,1,1,0,0,,good"],
    )
    mismatched = {**CONTEXT, "firmware_version": "UNKNOWN"}

    with pytest.raises(DefrostDiagnosticsError, match="controller/firmware binding"):
        engine_for(csv_path, context=mismatched)


def test_oem_exact_scope_is_blocked_without_external_approval_binding(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "oem.csv"
    write_csv(
        csv_path,
        ["2026-07-15T04:00:00,HP-01,heating,10,7,500,1600,8,70,4,5,1,1,0,0,,good"],
    )

    with pytest.raises(DefrostDiagnosticsError, match="oem_exact"):
        engine_for(csv_path, rules={**RULES, "compliance_scope": "oem_exact"})


def test_event_reconstruction_scope_is_blocked_without_external_approval_binding(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "event-reconstruction.csv"
    write_csv(
        csv_path,
        ["2026-07-15T04:00:00,HP-01,heating,10,7,500,1600,8,70,4,5,1,1,0,0,,good"],
    )

    with pytest.raises(DefrostDiagnosticsError, match="event_reconstruction"):
        engine_for(
            csv_path,
            rules={**RULES, "compliance_scope": "event_reconstruction"},
        )


def test_window_without_a_complete_defrost_event_is_unobservable(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "no-event.csv"
    write_csv(
        csv_path,
        [
            "2026-07-15T12:00:00,HP-01,heating,12,8,500,1600,8,70,4,5,1,1,0,0,,good",
            "2026-07-15T12:00:10,HP-01,heating,12,8,500,1600,8,70,4,5,1,1,0,0,,good",
        ],
    )

    result = engine_for(csv_path).analyze(
        asset_id="HP-01",
        start="2026-07-15T12:00:00",
        end="2026-07-15T12:00:20",
    )

    assert result.status == "unobservable"
    assert "no complete defrost event" in result.summary


def test_window_starting_mid_defrost_is_unobservable_not_an_entry_violation(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "mid-event.csv"
    write_csv(
        csv_path,
        [
            "2026-07-15T04:00:30,HP-01,defrost,2,3,430,1680,3,74,4,5,1,0,1,1,,good",
            "2026-07-15T04:00:40,HP-01,recovery,2,6,435,1660,4,71,4,5,1,0,0,0,,good",
            "2026-07-15T04:00:50,HP-01,heating,2,1,425,1690,3,75,4,5,1,1,0,0,,good",
        ],
    )

    result = engine_for(csv_path).analyze(
        asset_id="HP-01",
        start="2026-07-15T04:00:30",
        end="2026-07-15T04:01:00",
    )

    assert result.status == "unobservable"
    assert result.violation_count == 0
    assert "started before the requested window" in result.summary


def test_window_ending_during_active_defrost_is_unobservable(tmp_path: Path) -> None:
    csv_path = tmp_path / "truncated.csv"
    write_csv(
        csv_path,
        [
            "2026-07-15T03:59:00,HP-01,heating,10,7,500,1600,8,70,4,5,1,1,0,0,,good",
            "2026-07-15T03:59:10,HP-01,heating,2,-2,420,1700,2,76,4,5,1,1,0,0,,good",
            "2026-07-15T03:59:20,HP-01,heating,2,-2,420,1700,2,76,4,5,1,1,0,0,,good",
            "2026-07-15T03:59:30,HP-01,heating,2,-2,420,1700,2,76,4,5,1,1,0,0,,good",
            "2026-07-15T03:59:40,HP-01,defrost,2,3,430,1680,3,74,4,5,1,0,1,1,,good",
            "2026-07-15T03:59:50,HP-01,defrost,2,4,430,1680,3,74,4,5,1,0,1,1,,good",
        ],
    )

    result = engine_for(csv_path).analyze(
        asset_id="HP-01",
        start="2026-07-15T03:59:00",
        end="2026-07-15T04:00:00",
    )

    assert result.status == "unobservable"
    assert "continues beyond the requested window" in result.summary


def test_requested_window_must_be_fully_covered_by_samples(tmp_path: Path) -> None:
    csv_path = tmp_path / "coverage.csv"
    write_csv(
        csv_path,
        [
            "2026-07-15T12:00:00,HP-01,heating,12,8,500,1600,8,70,4,5,1,1,0,0,,good",
            "2026-07-15T12:00:10,HP-01,heating,12,8,500,1600,8,70,4,5,1,1,0,0,,good",
        ],
    )

    result = engine_for(csv_path).analyze(
        asset_id="HP-01",
        start="2026-07-15T12:00:00",
        end="2026-07-15T12:01:00",
    )

    assert result.status == "insufficient_data"
    assert "requested window is not fully covered" in result.summary


def test_rule_resolution_finer_than_sampling_is_unobservable(tmp_path: Path) -> None:
    csv_path = tmp_path / "resolution.csv"
    write_csv(
        csv_path,
        [
            "2026-07-15T12:00:00,HP-01,heating,12,8,500,1600,8,70,4,5,1,1,0,0,,good",
            "2026-07-15T12:00:10,HP-01,heating,12,8,500,1600,8,70,4,5,1,1,0,0,,good",
        ],
    )

    result = engine_for(
        csv_path,
        rules={**RULES, "required_resolution_seconds": 5},
    ).analyze(
        asset_id="HP-01",
        start="2026-07-15T12:00:00",
        end="2026-07-15T12:00:20",
    )

    assert result.status == "unobservable"
    assert "sampling interval is too coarse" in result.summary


def test_observed_sampling_interval_cannot_use_tolerance_to_bypass_resolution(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "observed-resolution.csv"
    write_csv(
        csv_path,
        [
            "2026-07-15T04:00:00,HP-01,heating,2,-2,420,1700,2,76,4,5,1,1,0,0,,good",
            "2026-07-15T04:00:12,HP-01,heating,2,-2,418,1702,2,77,4,5,1,1,0,0,,good",
            "2026-07-15T04:00:24,HP-01,defrost,2,-1,430,1680,3,74,4,5,1,0,1,1,,good",
            "2026-07-15T04:00:36,HP-01,defrost,2,3,440,1650,4,70,4,5,1,0,1,1,,good",
            "2026-07-15T04:00:48,HP-01,recovery,2,6,435,1660,4,71,4,5,1,0,0,0,,good",
            "2026-07-15T04:01:00,HP-01,heating,2,1,425,1690,3,75,4,5,1,1,0,0,,good",
        ],
    )

    result = engine_for(csv_path).analyze(
        asset_id="HP-01",
        start="2026-07-15T04:00:00",
        end="2026-07-15T04:01:10",
    )

    assert result.status == "unobservable"
    assert "observed sampling interval is too coarse" in result.summary
    assert result.timestamp_uncertainty_seconds == 12


def test_fractional_observed_interval_is_compared_without_truncation(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "fractional-resolution.csv"
    write_csv(
        csv_path,
        [
            "2026-07-15T04:00:00.000,HP-01,heating,2,-2,420,1700,2,76,4,5,1,1,0,0,,good",
            "2026-07-15T04:00:10.500,HP-01,heating,2,-2,418,1702,2,77,4,5,1,1,0,0,,good",
            "2026-07-15T04:00:21.000,HP-01,defrost,2,-1,430,1680,3,74,4,5,1,0,1,1,,good",
            "2026-07-15T04:00:31.500,HP-01,defrost,2,3,440,1650,4,70,4,5,1,0,1,1,,good",
            "2026-07-15T04:00:42.000,HP-01,recovery,2,6,435,1660,4,71,4,5,1,0,0,0,,good",
            "2026-07-15T04:00:52.500,HP-01,heating,2,1,425,1690,3,75,4,5,1,1,0,0,,good",
        ],
    )

    result = engine_for(csv_path).analyze(
        asset_id="HP-01",
        start="2026-07-15T04:00:00",
        end="2026-07-15T04:01:02.500",
    )

    assert result.status == "unobservable"
    assert result.timestamp_uncertainty_seconds == 11


def test_fractional_duration_overrun_is_not_truncated_and_records_actual_value(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "fractional-duration.csv"
    write_csv(
        csv_path,
        [
            "2026-07-15T04:00:00.000,HP-01,heating,2,-2,420,1700,2,76,4,5,1,1,0,0,,good",
            "2026-07-15T04:00:10.000,HP-01,heating,2,-2,418,1702,2,77,4,5,1,1,0,0,,good",
            "2026-07-15T04:00:20.000,HP-01,defrost,2,-1,430,1680,3,74,4,5,1,0,1,1,,good",
            "2026-07-15T04:00:30.000,HP-01,defrost,2,3,440,1650,4,70,4,5,1,0,1,1,,good",
            "2026-07-15T04:00:40.500,HP-01,defrost,2,3,440,1650,4,70,4,5,1,0,1,1,,good",
            "2026-07-15T04:00:50.500,HP-01,recovery,2,6,435,1660,4,71,4,5,1,0,0,0,,good",
            "2026-07-15T04:01:00.500,HP-01,heating,2,1,425,1690,3,75,4,5,1,1,0,0,,good",
        ],
    )

    result = engine_for(
        csv_path,
        rules={
            **RULES,
            "required_resolution_seconds": 11,
            "defrost_max_seconds": 20,
        },
    ).analyze(
        asset_id="HP-01",
        start="2026-07-15T04:00:00",
        end="2026-07-15T04:01:10.500",
    )

    violation = next(
        item for item in result.violations if item.code == "defrost_duration_exceeded"
    )
    assert result.status == "non_compliant"
    assert violation.expected["defrost_duration_seconds_max"] == 20
    assert violation.observed["defrost_duration_seconds"] == 20.5


def test_first_clear_sample_crossing_duration_limit_is_unobservable(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "interval-censored-exit.csv"
    write_csv(
        csv_path,
        [
            "2026-07-15T04:00:00.000,HP-01,heating,2,-2,420,1700,2,76,4,5,1,1,0,0,,good",
            "2026-07-15T04:00:10.000,HP-01,heating,2,-2,418,1702,2,77,4,5,1,1,0,0,,good",
            "2026-07-15T04:00:20.000,HP-01,defrost,2,-1,430,1680,3,74,4,5,1,0,1,1,,good",
            "2026-07-15T04:00:30.000,HP-01,defrost,2,3,440,1650,4,70,4,5,1,0,1,1,,good",
            "2026-07-15T04:00:40.500,HP-01,recovery,2,6,435,1660,4,71,4,5,1,0,0,0,,good",
            "2026-07-15T04:00:50.500,HP-01,heating,2,1,425,1690,3,75,4,5,1,1,0,0,,good",
        ],
    )

    result = engine_for(
        csv_path,
        rules={
            **RULES,
            "required_resolution_seconds": 11,
            "defrost_max_seconds": 20,
        },
    ).analyze(
        asset_id="HP-01",
        start="2026-07-15T04:00:00",
        end="2026-07-15T04:01:00.500",
    )

    assert result.status == "unobservable"
    assert result.violation_count == 0
    assert "clear transition crossed the maximum-duration threshold" in result.summary
    assert result.unobservable_reasons


def test_fan_restart_crossing_recovery_threshold_is_unobservable(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "interval-censored-recovery.csv"
    write_csv(
        csv_path,
        [
            "2026-07-15T04:00:00.000,HP-01,heating,2,-2,420,1700,2,76,4,5,1,1,0,0,,good",
            "2026-07-15T04:00:10.000,HP-01,heating,2,-2,418,1702,2,77,4,5,1,1,0,0,,good",
            "2026-07-15T04:00:20.000,HP-01,defrost,2,-1,430,1680,3,74,4,5,1,0,1,1,,good",
            "2026-07-15T04:00:30.000,HP-01,recovery,2,6,435,1660,4,71,4,5,1,0,0,0,,good",
            "2026-07-15T04:00:40.000,HP-01,recovery,2,6,435,1660,4,71,4,5,1,0,0,0,,good",
            "2026-07-15T04:00:50.500,HP-01,heating,2,1,425,1690,3,75,4,5,1,1,0,0,,good",
        ],
    )

    result = engine_for(
        csv_path,
        rules={
            **RULES,
            "required_resolution_seconds": 11,
            "recovery_min_seconds": 20,
        },
    ).analyze(
        asset_id="HP-01",
        start="2026-07-15T04:00:00",
        end="2026-07-15T04:01:00.500",
    )

    assert result.status == "unobservable"
    assert "fan restart crossed the recovery threshold" in result.summary

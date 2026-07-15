from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from project_copilot.platform_compat import ensure_windows_architecture_env


ensure_windows_architecture_env()

import pandera.polars as pa  # noqa: E402
import polars as pl  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field  # noqa: E402
from transitions import Machine  # noqa: E402


class DefrostDiagnosticsError(ValueError):
    """Raised when a defrost rule pack or telemetry request is invalid."""


class DefrostRulePack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1.0"
    rule_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)
    controller_model: str = Field(min_length=1)
    firmware_version: str = Field(min_length=1)
    compliance_scope: Literal["synthetic_demo", "event_reconstruction", "oem_exact"]
    timezone: str = Field(min_length=1)
    source_file: str = Field(min_length=1)
    source_section: str = Field(min_length=1)
    sample_interval_seconds: int = Field(gt=0, le=3600)
    required_resolution_seconds: int = Field(gt=0, le=3600)
    gap_tolerance_seconds: int = Field(ge=0, le=300)
    candidate_outdoor_temp_c_max: float
    candidate_coil_temp_c_max: float
    candidate_min_seconds: int = Field(gt=0)
    initiation_max_delay_seconds: int = Field(gt=0)
    defrost_max_seconds: int = Field(gt=0)
    exit_coil_temp_c_min: float
    recovery_min_seconds: int = Field(ge=0)
    defrost_fan_expected: int = Field(ge=0, le=1)
    defrost_reversing_valve_expected: int = Field(ge=0, le=1)


class DefrostAssetContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1.0"
    asset_id: str = Field(min_length=1)
    controller_model: str = Field(min_length=1)
    firmware_version: str = Field(min_length=1)
    source_file: str = Field(min_length=1)
    source_section: str = Field(min_length=1)


@dataclass(frozen=True)
class DefrostTransition:
    at: str
    from_state: str
    to_state: str
    reason: str


@dataclass(frozen=True)
class DefrostViolation:
    event_id: str
    rule_id: str
    code: str
    at: str
    message: str
    expected: dict[str, object]
    observed: dict[str, object]


@dataclass(frozen=True)
class DefrostDiagnosticResult:
    status: Literal["compliant", "non_compliant", "insufficient_data", "unobservable"]
    asset_id: str
    window_start: str
    window_end: str
    sample_count: int
    violation_count: int
    first_deviation_at: str | None
    summary: str
    rule_id: str
    rule_version: str
    rule_source: str
    rule_section: str
    controller_model: str
    firmware_version: str
    compliance_scope: str
    timezone: str
    timestamp_uncertainty_seconds: int
    transitions: tuple[DefrostTransition, ...]
    violations: tuple[DefrostViolation, ...]
    unobservable_reasons: tuple[str, ...] = ()


DEFROST_TELEMETRY_SCHEMA = pa.DataFrameSchema(
    {
        "timestamp": pa.Column(pl.Datetime),
        "asset_id": pa.Column(str),
        "mode": pa.Column(
            str, checks=pa.Check.isin(["heating", "defrost", "recovery"])
        ),
        "outdoor_temp_c": pa.Column(float, checks=pa.Check.in_range(-60, 70)),
        "outdoor_coil_temp_c": pa.Column(float, checks=pa.Check.in_range(-80, 100)),
        "suction_pressure_kpa": pa.Column(float, checks=pa.Check.in_range(0, 5000)),
        "discharge_pressure_kpa": pa.Column(float, checks=pa.Check.in_range(0, 8000)),
        "suction_temp_c": pa.Column(float, checks=pa.Check.in_range(-80, 120)),
        "discharge_temp_c": pa.Column(float, checks=pa.Check.in_range(-80, 220)),
        "superheat_k": pa.Column(float, checks=pa.Check.in_range(-20, 100)),
        "subcooling_k": pa.Column(float, checks=pa.Check.in_range(-20, 100)),
        "compressor_command": pa.Column(int, checks=pa.Check.isin([0, 1])),
        "outdoor_fan_command": pa.Column(int, checks=pa.Check.isin([0, 1])),
        "reversing_valve_command": pa.Column(int, checks=pa.Check.isin([0, 1])),
        "defrost_command": pa.Column(int, checks=pa.Check.isin([0, 1])),
        "alarm_code": pa.Column(str, nullable=True),
        "data_quality": pa.Column(str),
    },
    strict=False,
    coerce=True,
)


class _ReplayState:
    pass


class DefrostDiagnosticsEngine:
    """Replay a reviewed defrost rule pack against a bounded telemetry window."""

    def __init__(
        self,
        csv_path: str | Path,
        rules: DefrostRulePack,
        asset_context: DefrostAssetContext,
    ) -> None:
        self.csv_path = Path(csv_path).resolve()
        self.rules = rules
        self.asset_context = asset_context
        if rules.compliance_scope != "synthetic_demo":
            raise DefrostDiagnosticsError(
                f"{rules.compliance_scope} is blocked until an external approval "
                "manifest binds the immutable telemetry, rule pack, point schedule, "
                "asset identity, controller, and firmware hashes"
            )
        expected_binding = (
            rules.asset_id,
            rules.controller_model,
            rules.firmware_version,
        )
        actual_binding = (
            asset_context.asset_id,
            asset_context.controller_model,
            asset_context.firmware_version,
        )
        if actual_binding != expected_binding:
            raise DefrostDiagnosticsError(
                "Defrost rule pack controller/firmware binding does not match "
                "the approved asset context"
            )

    def analyze(
        self, *, asset_id: str, start: str | datetime, end: str | datetime
    ) -> DefrostDiagnosticResult:
        if asset_id != self.rules.asset_id:
            raise DefrostDiagnosticsError(
                f"Rule pack {self.rules.rule_id} is approved only for {self.rules.asset_id}"
            )
        start_at = self._local_datetime(start)
        end_at = self._local_datetime(end)
        if start_at >= end_at:
            raise DefrostDiagnosticsError("Analysis end must be after start")

        try:
            frame = pl.read_csv(self.csv_path, try_parse_dates=True)
            frame = DEFROST_TELEMETRY_SCHEMA.validate(frame)
        except (OSError, pl.exceptions.PolarsError, pa.errors.SchemaError) as exc:
            raise DefrostDiagnosticsError(
                f"Defrost telemetry validation failed: {exc}"
            ) from exc

        selected = frame.filter(
            (pl.col("asset_id") == asset_id)
            & (pl.col("timestamp") >= pl.lit(start_at))
            & (pl.col("timestamp") < pl.lit(end_at))
        ).sort("timestamp")
        observed_interval_seconds = self._observed_interval_seconds(selected)
        effective_interval_seconds = max(
            self.rules.sample_interval_seconds, observed_interval_seconds
        )
        timestamp_uncertainty_seconds = ceil(effective_interval_seconds)
        quality_problem = self._quality_problem(selected, start_at, end_at)
        if quality_problem:
            return self._result(
                status="insufficient_data",
                asset_id=asset_id,
                start_at=start_at,
                end_at=end_at,
                sample_count=selected.height,
                transitions=[],
                violations=[],
                summary=f"Evidence is insufficient: {quality_problem}.",
                timestamp_uncertainty_seconds=timestamp_uncertainty_seconds,
            )
        if effective_interval_seconds > self.rules.required_resolution_seconds:
            return self._result(
                status="unobservable",
                asset_id=asset_id,
                start_at=start_at,
                end_at=end_at,
                sample_count=selected.height,
                transitions=[],
                violations=[],
                summary=(
                    "The observed sampling interval is too coarse for the required "
                    "rule resolution, so event ordering is unobservable."
                ),
                timestamp_uncertainty_seconds=timestamp_uncertainty_seconds,
            )

        first_row = selected.row(0, named=True)
        if first_row["defrost_command"] == 1 or first_row["mode"] in {
            "defrost",
            "recovery",
        }:
            return self._result(
                status="unobservable",
                asset_id=asset_id,
                start_at=start_at,
                end_at=end_at,
                sample_count=selected.height,
                transitions=[],
                violations=[],
                summary=(
                    "The defrost lifecycle started before the requested window; "
                    "entry timing and candidate dwell are unobservable."
                ),
                timestamp_uncertainty_seconds=timestamp_uncertainty_seconds,
            )

        previous = (
            frame.filter(
                (pl.col("asset_id") == asset_id)
                & (pl.col("timestamp") < pl.lit(start_at))
            )
            .sort("timestamp")
            .tail(1)
        )

        def qualifies(row: dict[str, object]) -> bool:
            return bool(
                row["mode"] == "heating"
                and row["compressor_command"] == 1
                and row["outdoor_temp_c"] <= self.rules.candidate_outdoor_temp_c_max
                and row["outdoor_coil_temp_c"] <= self.rules.candidate_coil_temp_c_max
            )

        if (
            qualifies(first_row)
            and not previous.is_empty()
            and qualifies(previous.row(0, named=True))
        ):
            return self._result(
                status="unobservable",
                asset_id=asset_id,
                start_at=start_at,
                end_at=end_at,
                sample_count=selected.height,
                transitions=[],
                violations=[],
                summary=(
                    "The candidate dwell started before the requested window, "
                    "so its initiation timing is unobservable."
                ),
                timestamp_uncertainty_seconds=timestamp_uncertainty_seconds,
            )

        state = _ReplayState()
        machine = Machine(
            model=state,
            states=["heating", "candidate", "defrost", "recovery"],
            initial="heating",
            auto_transitions=False,
        )
        machine.add_transition("candidate_detected", "heating", "candidate")
        machine.add_transition("candidate_cleared", "candidate", "heating")
        machine.add_transition("start_defrost", ["heating", "candidate"], "defrost")
        machine.add_transition("finish_defrost", "defrost", "recovery")
        machine.add_transition("finish_recovery", "recovery", "heating")

        transitions: list[DefrostTransition] = []
        violations: list[DefrostViolation] = []
        unobservable_reasons: list[str] = []
        emitted_codes: set[str] = set()
        candidate_since: datetime | None = None
        defrost_since: datetime | None = None
        recovery_since: datetime | None = None
        last_defrost_active_at: datetime | None = None
        last_recovery_sample_at: datetime | None = None
        event_number = 0

        def begin_event() -> None:
            nonlocal event_number
            event_number += 1
            emitted_codes.clear()

        def transition(trigger: str, at: datetime, reason: str) -> None:
            previous = str(state.state)
            getattr(state, trigger)()
            transitions.append(
                DefrostTransition(
                    at=str(at),
                    from_state=previous,
                    to_state=str(state.state),
                    reason=reason,
                )
            )

        def violate(
            code: str,
            at: datetime,
            message: str,
            expected: dict[str, object],
            row: dict[str, object],
            observed_extra: dict[str, object] | None = None,
        ) -> None:
            if code in emitted_codes:
                return
            emitted_codes.add(code)
            observed_keys = (
                "outdoor_temp_c",
                "outdoor_coil_temp_c",
                "compressor_command",
                "outdoor_fan_command",
                "reversing_valve_command",
                "defrost_command",
            )
            observed = {key: row[key] for key in observed_keys}
            observed.update(observed_extra or {})
            violations.append(
                DefrostViolation(
                    event_id=f"event-{event_number:03d}",
                    rule_id=self.rules.rule_id,
                    code=code,
                    at=str(at),
                    message=message,
                    expected=expected,
                    observed=observed,
                )
            )

        for row in selected.iter_rows(named=True):
            at = row["timestamp"]
            is_candidate = qualifies(row)

            if state.state == "heating":
                if row["defrost_command"] == 1:
                    begin_event()
                    violate(
                        "entry_without_candidate",
                        at,
                        "Defrost started without a qualified candidate dwell.",
                        {
                            "candidate_dwell_seconds_min": self.rules.candidate_min_seconds,
                            "defrost_command_after_candidate": 1,
                        },
                        row,
                        {"candidate_dwell_seconds": 0.0},
                    )
                    transition("start_defrost", at, "observed defrost command")
                    defrost_since = at
                elif is_candidate:
                    transition("candidate_detected", at, "entry predicates satisfied")
                    candidate_since = at

            elif state.state == "candidate":
                candidate_seconds = (at - candidate_since).total_seconds()
                if row["defrost_command"] == 1:
                    begin_event()
                    if candidate_seconds < self.rules.candidate_min_seconds:
                        violate(
                            "candidate_dwell_too_short",
                            at,
                            "Defrost started before the candidate dwell completed.",
                            {
                                "candidate_dwell_seconds_min": self.rules.candidate_min_seconds
                            },
                            row,
                            {"candidate_dwell_seconds": candidate_seconds},
                        )
                    elif candidate_seconds > self.rules.initiation_max_delay_seconds:
                        violate(
                            "defrost_started_after_max_delay",
                            at,
                            "Defrost started after the approved initiation delay.",
                            {
                                "initiation_delay_seconds_max": self.rules.initiation_max_delay_seconds
                            },
                            row,
                            {"initiation_delay_seconds": candidate_seconds},
                        )
                    transition("start_defrost", at, "observed defrost command")
                    defrost_since = at
                elif not is_candidate:
                    transition("candidate_cleared", at, "entry predicates cleared")
                    candidate_since = None
                elif candidate_seconds > self.rules.initiation_max_delay_seconds:
                    violate(
                        "qualified_candidate_not_started",
                        at,
                        "Qualified defrost demand exceeded the allowed initiation delay.",
                        {
                            "initiation_delay_seconds_max": self.rules.initiation_max_delay_seconds,
                            "defrost_command": 1,
                        },
                        row,
                        {"initiation_delay_seconds": candidate_seconds},
                    )

            if state.state == "defrost":
                if (
                    row["defrost_command"] == 1
                    and row["outdoor_fan_command"] != self.rules.defrost_fan_expected
                ):
                    violate(
                        "outdoor_fan_on_during_defrost",
                        at,
                        "Outdoor fan command did not match the approved defrost state.",
                        {"outdoor_fan_command": self.rules.defrost_fan_expected},
                        row,
                    )
                if (
                    row["defrost_command"] == 1
                    and row["reversing_valve_command"]
                    != self.rules.defrost_reversing_valve_expected
                ):
                    violate(
                        "reversing_valve_state_mismatch",
                        at,
                        "Reversing-valve command did not match the approved defrost state.",
                        {
                            "reversing_valve_command": self.rules.defrost_reversing_valve_expected
                        },
                        row,
                    )
                defrost_seconds = (at - defrost_since).total_seconds()
                if row["defrost_command"] == 0:
                    maximum_end_at = defrost_since + timedelta(
                        seconds=self.rules.defrost_max_seconds
                    )
                    if (
                        last_defrost_active_at is not None
                        and last_defrost_active_at <= maximum_end_at < at
                    ):
                        reason = (
                            "The first clear transition crossed the maximum-duration "
                            "threshold between samples, so that clause is unobservable."
                        )
                        if reason not in unobservable_reasons:
                            unobservable_reasons.append(reason)
                    if (
                        row["outdoor_coil_temp_c"] < self.rules.exit_coil_temp_c_min
                        and defrost_seconds < self.rules.defrost_max_seconds
                    ):
                        violate(
                            "defrost_ended_before_exit_condition",
                            at,
                            "Defrost ended before the temperature or maximum-time exit condition.",
                            {
                                "outdoor_coil_temp_c_min": self.rules.exit_coil_temp_c_min,
                                "or_defrost_duration_seconds_max": self.rules.defrost_max_seconds,
                            },
                            row,
                            {"defrost_duration_seconds": defrost_seconds},
                        )
                    transition("finish_defrost", at, "observed defrost command cleared")
                    recovery_since = at
                    last_recovery_sample_at = at
                elif defrost_seconds > self.rules.defrost_max_seconds:
                    violate(
                        "defrost_duration_exceeded",
                        at,
                        "Defrost command exceeded the approved maximum duration.",
                        {
                            "defrost_duration_seconds_max": self.rules.defrost_max_seconds
                        },
                        row,
                        {"defrost_duration_seconds": defrost_seconds},
                    )
                if row["defrost_command"] == 1:
                    last_defrost_active_at = at

            elif state.state == "recovery":
                recovery_seconds = (at - recovery_since).total_seconds()
                if (
                    recovery_seconds < self.rules.recovery_min_seconds
                    and row["outdoor_fan_command"] == 1
                ):
                    violate(
                        "outdoor_fan_started_during_recovery_delay",
                        at,
                        "Outdoor fan started before the recovery delay completed.",
                        {
                            "outdoor_fan_command": 0,
                            "recovery_delay_seconds_min": self.rules.recovery_min_seconds,
                        },
                        row,
                        {"recovery_delay_seconds": recovery_seconds},
                    )
                elif row["outdoor_fan_command"] == 1:
                    allowed_restart_at = recovery_since + timedelta(
                        seconds=self.rules.recovery_min_seconds
                    )
                    if (
                        last_recovery_sample_at is not None
                        and last_recovery_sample_at < allowed_restart_at < at
                    ):
                        reason = (
                            "The first fan restart crossed the recovery threshold "
                            "between samples, so that clause is unobservable."
                        )
                        if reason not in unobservable_reasons:
                            unobservable_reasons.append(reason)
                if recovery_seconds >= self.rules.recovery_min_seconds:
                    transition("finish_recovery", at, "recovery dwell completed")
                last_recovery_sample_at = at

        if violations:
            status = "non_compliant"
            summary = (
                f"Defrost logic was non-compliant: {len(violations)} violation(s); "
                f"first deviation at {violations[0].at}. Review the structured "
                "expected-versus-observed deviations below."
            )
            if unobservable_reasons:
                summary += " Other timing clauses remain unobservable between samples."
        elif unobservable_reasons:
            status = "unobservable"
            summary = " ".join(unobservable_reasons)
        elif event_number == 0:
            status = "unobservable"
            summary = (
                "The requested window contains no complete defrost event, so "
                "defrost compliance is not observable."
            )
        elif state.state != "heating":
            status = "unobservable"
            summary = (
                "The observed defrost lifecycle continues beyond the requested "
                "window, so a complete compliance verdict is unobservable."
            )
        else:
            status = "compliant"
            summary = (
                f"Defrost logic was compliant across {selected.height} samples with "
                "0 violations."
            )
        return self._result(
            status=status,
            asset_id=asset_id,
            start_at=start_at,
            end_at=end_at,
            sample_count=selected.height,
            transitions=transitions,
            violations=violations,
            summary=summary,
            timestamp_uncertainty_seconds=timestamp_uncertainty_seconds,
            unobservable_reasons=unobservable_reasons,
        )

    def _observed_interval_seconds(self, frame: pl.DataFrame) -> float:
        intervals = (
            frame["timestamp"].diff().dt.total_milliseconds().drop_nulls() / 1000.0
        )
        if intervals.is_empty():
            return float(self.rules.sample_interval_seconds)
        return max(float(value) for value in intervals)

    def _quality_problem(
        self, frame: pl.DataFrame, start_at: datetime, end_at: datetime
    ) -> str | None:
        if frame.is_empty():
            return "no samples exist in the requested asset/time window"
        if frame["timestamp"].n_unique() != frame.height:
            return "duplicate timestamp detected"
        if frame.filter(pl.col("data_quality") != "good").height:
            return "one or more samples failed the approved data-quality flag"
        intervals = (
            frame["timestamp"].diff().dt.total_milliseconds().drop_nulls() / 1000.0
        )
        expected = self.rules.sample_interval_seconds
        tolerance = self.rules.gap_tolerance_seconds
        first_at = frame["timestamp"][0]
        last_at = frame["timestamp"][-1]
        expected_last = end_at - timedelta(seconds=expected)
        if (first_at - start_at).total_seconds() > tolerance or (
            expected_last - last_at
        ).total_seconds() > tolerance:
            return "requested window is not fully covered by telemetry samples"
        if any(abs(float(value) - expected) > tolerance for value in intervals):
            return "sampling interval gap or drift exceeds the approved tolerance"
        return None

    def _local_datetime(self, value: str | datetime) -> datetime:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(ZoneInfo(self.rules.timezone)).replace(
                tzinfo=None
            )
        return parsed

    def _result(
        self,
        *,
        status: str,
        asset_id: str,
        start_at: datetime,
        end_at: datetime,
        sample_count: int,
        transitions: list[DefrostTransition],
        violations: list[DefrostViolation],
        summary: str,
        timestamp_uncertainty_seconds: int | None = None,
        unobservable_reasons: list[str] | None = None,
    ) -> DefrostDiagnosticResult:
        return DefrostDiagnosticResult(
            status=status,
            asset_id=asset_id,
            window_start=str(start_at),
            window_end=str(end_at),
            sample_count=sample_count,
            violation_count=len(violations),
            first_deviation_at=violations[0].at if violations else None,
            summary=summary,
            rule_id=self.rules.rule_id,
            rule_version=self.rules.version,
            rule_source=self.rules.source_file,
            rule_section=self.rules.source_section,
            controller_model=self.rules.controller_model,
            firmware_version=self.rules.firmware_version,
            compliance_scope=self.rules.compliance_scope,
            timezone=self.rules.timezone,
            timestamp_uncertainty_seconds=(
                timestamp_uncertainty_seconds or self.rules.sample_interval_seconds
            ),
            transitions=tuple(transitions),
            violations=tuple(violations),
            unobservable_reasons=tuple(unobservable_reasons or ()),
        )

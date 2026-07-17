from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import re
from typing import Any

import duckdb


class SnapshotInspectionError(ValueError):
    """Raised when a snapshot inspection is unavailable or not allowlisted."""


@dataclass(frozen=True)
class SnapshotInspectionResult:
    operation: str
    title: str
    summary: str
    rows: list[dict[str, Any]]


class HVACSnapshotInspector:
    """Typed, read-only HVAC event inspection over an approved DuckDB snapshot."""

    OPERATIONS = {"data_quality", "control_events", "alarm_events"}
    METRICS = {
        "suction_pressure_kpa_g": "kPa(g)",
        "discharge_pressure_kpa_g": "kPa(g)",
        "suction_temp_c": "°C",
        "discharge_temp_c": "°C",
        "superheat_k": "K",
        "subcooling_k": "K",
        "electric_power_kw": "kW",
        "thermal_output_kw": "kW",
        "cop": "",
    }

    def __init__(
        self,
        database_path: str | Path,
        *,
        timezone_name: str | None = None,
        sample_seconds: int = 10,
    ) -> None:
        self.database_path = Path(database_path).resolve()
        self.timezone_name = timezone_name
        if sample_seconds <= 0:
            raise SnapshotInspectionError("sample_seconds must be positive")
        self.sample_seconds = int(sample_seconds)
        if not self.database_path.is_file():
            raise SnapshotInspectionError("HVAC snapshot database is unavailable")

    def inspect(self, operation: str) -> SnapshotInspectionResult:
        if operation not in self.OPERATIONS:
            raise SnapshotInspectionError(
                f"Snapshot inspection operation is not allowlisted: {operation}"
            )
        if operation == "data_quality":
            return self._data_quality()
        if operation == "control_events":
            return self._control_events()
        return self._alarm_events()

    def _connect(self) -> duckdb.DuckDBPyConnection:
        config = {
            "enable_external_access": "false",
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
            "allow_community_extensions": "false",
            "memory_limit": "256MB",
            "threads": "2",
            "max_temp_directory_size": "0GB",
        }
        connection = duckdb.connect(
            str(self.database_path),
            read_only=True,
            config=config,
        )
        if self.timezone_name:
            connection.execute("SET TimeZone = ?", [self.timezone_name])
        return connection

    @staticmethod
    def _value(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, float):
            return round(value, 6)
        if isinstance(value, str) and re.match(
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[+-]\d{2}(?::?\d{2})?$",
            value,
        ):
            normalized = value.replace(" ", "T", 1)
            if re.search(r"[+-]\d{2}$", normalized):
                normalized += ":00"
            elif re.search(r"[+-]\d{4}$", normalized):
                normalized = f"{normalized[:-2]}:{normalized[-2:]}"
            return normalized
        return value

    def _query(
        self,
        connection: duckdb.DuckDBPyConnection,
        sql: str,
        parameters: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        cursor = connection.execute(sql, parameters or [])
        columns = [item[0] for item in cursor.description]
        return [
            {
                column: self._value(value)
                for column, value in zip(columns, row, strict=True)
            }
            for row in cursor.fetchall()
        ]

    def _data_quality(self) -> SnapshotInspectionResult:
        connection = self._connect()
        try:
            rows = self._query(
                connection,
                f"""
                WITH bounds AS (
                    SELECT min(timestamp) AS start_time, max(timestamp) AS end_time
                    FROM telemetry_clean
                )
                SELECT
                    'coverage' AS event_type,
                    asset_id,
                    CAST(min(timestamp) AS VARCHAR) AS start_time,
                    CAST(max(timestamp) AS VARCHAR) AS end_time,
                    count(*) AS sample_count,
                    CAST(date_diff('second', bounds.start_time, bounds.end_time)
                         / {self.sample_seconds} + 1 AS BIGINT) AS expected_samples,
                    CAST(date_diff('second', bounds.start_time, bounds.end_time)
                         / {self.sample_seconds} + 1 - count(*) AS BIGINT)
                        AS missing_samples,
                    round(
                        count(*) * 100.0
                        / (date_diff('second', bounds.start_time, bounds.end_time)
                           / {self.sample_seconds} + 1),
                        6
                    ) AS completeness_pct
                FROM telemetry_clean, bounds
                GROUP BY asset_id, bounds.start_time, bounds.end_time
                ORDER BY asset_id
                """,
            )
            rows.extend(
                self._query(
                    connection,
                    """
                    WITH duplicate_keys AS (
                        SELECT asset_id, timestamp, count(*) AS row_count
                        FROM telemetry_raw
                        GROUP BY asset_id, timestamp
                        HAVING count(*) > 1
                    )
                    SELECT
                        'duplicate_timestamp' AS event_type,
                        asset_id,
                        CAST(min(timestamp) AS VARCHAR) AS start_time,
                        CAST(max(timestamp) AS VARCHAR) AS end_time,
                        count(*) AS event_count,
                        sum(row_count - 1) AS duplicate_rows
                    FROM duplicate_keys
                    GROUP BY asset_id
                    ORDER BY asset_id
                    """,
                )
            )
            rows.extend(
                self._query(
                    connection,
                    """
                    WITH ordered AS (
                        SELECT
                            asset_id,
                            timestamp,
                            lag(timestamp) OVER (
                                PARTITION BY asset_id ORDER BY ingest_seq
                            ) AS previous_timestamp
                        FROM telemetry_raw
                    )
                    SELECT
                        'out_of_order' AS event_type,
                        asset_id,
                        CAST(min(timestamp) AS VARCHAR) AS start_time,
                        CAST(max(timestamp) AS VARCHAR) AS end_time,
                        count(*) AS event_count
                    FROM ordered
                    WHERE timestamp < previous_timestamp
                    GROUP BY asset_id
                    ORDER BY asset_id
                    """,
                )
            )
            rows.extend(
                self._query(
                    connection,
                    f"""
                    WITH signatures AS (
                        SELECT
                            asset_id,
                            timestamp,
                            hash(
                                ambient_temp_c,
                                return_air_temp_c,
                                supply_air_temp_c,
                                suction_temp_c,
                                discharge_temp_c,
                                liquid_temp_c,
                                suction_pressure_kpa_g,
                                discharge_pressure_kpa_g,
                                compressor_fb_hz
                            ) AS signature
                        FROM telemetry_clean
                    ), boundaries AS (
                        SELECT
                            *,
                            CASE
                                WHEN signature = lag(signature) OVER (
                                    PARTITION BY asset_id ORDER BY timestamp
                                )
                                AND date_diff(
                                    'second',
                                    lag(timestamp) OVER (
                                        PARTITION BY asset_id ORDER BY timestamp
                                    ),
                                    timestamp
                                ) = {self.sample_seconds}
                                THEN 0 ELSE 1
                            END AS new_group
                        FROM signatures
                    ), grouped AS (
                        SELECT
                            *,
                            sum(new_group) OVER (
                                PARTITION BY asset_id ORDER BY timestamp
                            ) AS group_id
                        FROM boundaries
                    )
                    SELECT
                        'frozen_sensor_tuple' AS event_type,
                        asset_id,
                        CAST(min(timestamp) AS VARCHAR) AS start_time,
                        CAST(
                            max(timestamp) + INTERVAL {self.sample_seconds} SECOND
                            AS VARCHAR
                        ) AS end_time,
                        count(*) AS sample_count,
                        count(*) * {self.sample_seconds} AS duration_seconds
                    FROM grouped
                    GROUP BY asset_id, group_id
                    HAVING count(*) >= 6
                    ORDER BY sample_count DESC, asset_id
                    """,
                )
            )
        finally:
            connection.close()
        return SnapshotInspectionResult(
            operation="data_quality",
            title="数据质量与完整率盘点",
            summary=(
                "已在当前只读快照中统一检查到报完整率、重复时间戳、"
                "上传顺序回退和连续冻结的传感器元组。"
            ),
            rows=rows,
        )

    def metric_extreme(
        self,
        metric: str,
        direction: str,
        asset_id: str | None = None,
    ) -> SnapshotInspectionResult:
        if metric not in self.METRICS:
            raise SnapshotInspectionError(
                f"Snapshot metric is not allowlisted: {metric}"
            )
        if direction not in {"minimum", "maximum"}:
            raise SnapshotInspectionError(
                f"Snapshot direction is not allowlisted: {direction}"
            )
        aggregate = "min" if direction == "minimum" else "max"
        event_type = f"metric_{direction}"
        selected_asset = (asset_id or "").strip()
        connection = self._connect()
        try:
            rows = self._query(
                connection,
                f"""
                WITH filtered AS (
                    SELECT *
                    FROM telemetry_clean
                    WHERE (? = '' OR asset_id = ?)
                ), target AS (
                    SELECT {aggregate}({metric}) AS extreme_value
                    FROM filtered
                ), matching AS (
                    SELECT filtered.*, target.extreme_value
                    FROM filtered, target
                    WHERE {metric} = target.extreme_value
                ), boundaries AS (
                    SELECT
                        *,
                        CASE
                            WHEN date_diff(
                                'second',
                                lag(timestamp) OVER (
                                    PARTITION BY asset_id ORDER BY timestamp
                                ),
                                timestamp
                            ) = {self.sample_seconds}
                            THEN 0 ELSE 1
                        END AS new_group
                    FROM matching
                ), grouped AS (
                    SELECT
                        *,
                        sum(new_group) OVER (
                            PARTITION BY asset_id ORDER BY timestamp
                        ) AS group_id
                    FROM boundaries
                )
                SELECT
                    '{event_type}' AS event_type,
                    asset_id,
                    '{metric}' AS metric,
                    '{self.METRICS[metric]}' AS unit,
                    round(extreme_value, 6) AS extreme_value,
                    CAST(min(timestamp) AS VARCHAR) AS start_time,
                    CAST(
                        max(timestamp) + INTERVAL {self.sample_seconds} SECOND
                        AS VARCHAR
                    ) AS end_time,
                    count(*) AS sample_count,
                    count(*) * {self.sample_seconds} AS duration_seconds,
                    round(avg(thermal_output_kw), 6)
                        AS average_thermal_output_kw,
                    round(avg(cop), 6) AS average_cop,
                    round(avg(superheat_k), 6) AS average_superheat_k
                FROM grouped
                GROUP BY asset_id, group_id, extreme_value
                ORDER BY start_time, asset_id
                """,
                [selected_asset, selected_asset],
            )
        finally:
            connection.close()
        return SnapshotInspectionResult(
            operation="metric_extreme",
            title=f"{metric} 的{direction}窗口",
            summary=(
                "已在当前只读快照中定位精确极值及其持续窗口，并同步计算能力、"
                "COP 和过热度；极值是观测结果，不等同于批准报警阈值。"
            ),
            rows=rows,
        )

    def _control_events(self) -> SnapshotInspectionResult:
        connection = self._connect()
        try:
            rows: list[dict[str, Any]] = []
            definitions = (
                (
                    "compressor_feedback_mismatch",
                    "compressor_cmd_hz > 0 AND abs(compressor_cmd_hz - compressor_fb_hz) > 5",
                    "abs(compressor_cmd_hz - compressor_fb_hz)",
                    "compressor_cmd_hz",
                    "compressor_fb_hz",
                ),
                (
                    "eev_feedback_mismatch",
                    "abs(eev_cmd_pct - eev_fb_pct) > 10",
                    "abs(eev_cmd_pct - eev_fb_pct)",
                    "eev_cmd_pct",
                    "eev_fb_pct",
                ),
                (
                    "outdoor_fan_feedback_mismatch",
                    "abs(outdoor_fan_cmd_pct - outdoor_fan_fb_pct) > 20",
                    "abs(outdoor_fan_cmd_pct - outdoor_fan_fb_pct)",
                    "outdoor_fan_cmd_pct",
                    "outdoor_fan_fb_pct",
                ),
                (
                    "indoor_fan_feedback_mismatch",
                    "abs(indoor_fan_cmd_pct - indoor_fan_fb_pct) > 20",
                    "abs(indoor_fan_cmd_pct - indoor_fan_fb_pct)",
                    "indoor_fan_cmd_pct",
                    "indoor_fan_fb_pct",
                ),
            )
            for event_type, predicate, deviation, command, feedback in definitions:
                event_type_expression = (
                    "CASE WHEN count(*) * "
                    f"{self.sample_seconds} >= 60 THEN "
                    "'compressor_feedback_mismatch' ELSE "
                    "'compressor_feedback_mismatch_observation' END"
                    if event_type == "compressor_feedback_mismatch"
                    else f"'{event_type}'"
                )
                rows.extend(
                    self._query(
                        connection,
                        f"""
                        WITH matching AS (
                            SELECT *, {deviation} AS deviation
                            FROM telemetry_clean
                            WHERE {predicate}
                        ), boundaries AS (
                            SELECT
                                *,
                                CASE
                                    WHEN date_diff(
                                        'second',
                                        lag(timestamp) OVER (
                                            PARTITION BY asset_id ORDER BY timestamp
                                        ),
                                        timestamp
                                    ) = {self.sample_seconds}
                                    THEN 0 ELSE 1
                                END AS new_group
                            FROM matching
                        ), grouped AS (
                            SELECT
                                *,
                                sum(new_group) OVER (
                                    PARTITION BY asset_id ORDER BY timestamp
                                ) AS group_id
                            FROM boundaries
                        )
                        SELECT
                            {event_type_expression} AS event_type,
                            asset_id,
                            CAST(min(timestamp) AS VARCHAR) AS start_time,
                            CAST(
                                max(timestamp)
                                + INTERVAL {self.sample_seconds} SECOND
                                AS VARCHAR
                            ) AS end_time,
                            count(*) AS sample_count,
                            count(*) * {self.sample_seconds} AS duration_seconds,
                            round(avg(deviation), 6) AS average_deviation,
                            round(max(deviation), 6) AS max_deviation,
                            round(avg({command}), 6) AS average_command,
                            round(avg({feedback}), 6) AS average_feedback,
                            round(min({command}), 6) AS min_command,
                            round(min({feedback}), 6) AS min_feedback,
                            round(max({feedback}), 6) AS max_feedback,
                            max(alarm_code) AS alarm_code
                        FROM grouped
                        GROUP BY asset_id, group_id
                        ORDER BY start_time, asset_id
                        """,
                    )
                )
            rows.extend(
                self._query(
                    connection,
                    """
                    WITH transitions AS (
                        SELECT
                            asset_id,
                            timestamp,
                            enable_cmd,
                            lag(enable_cmd) OVER (
                                PARTITION BY asset_id ORDER BY timestamp
                            ) AS previous_enable
                        FROM telemetry_clean
                    ), hourly AS (
                        SELECT
                            asset_id,
                            date_trunc('hour', timestamp) AS start_time,
                            sum(
                                CASE
                                    WHEN enable_cmd = 1
                                    AND previous_enable = 0
                                    THEN 1 ELSE 0
                                END
                            ) AS start_count
                        FROM transitions
                        GROUP BY asset_id, date_trunc('hour', timestamp)
                    )
                    SELECT
                        'short_cycling' AS event_type,
                        asset_id,
                        CAST(start_time AS VARCHAR) AS start_time,
                        CAST(
                            start_time + INTERVAL 1 HOUR
                            AS VARCHAR
                        ) AS end_time,
                        start_count,
                        4 AS threshold_start_count,
                        round((start_count - 4) * 100.0 / 4, 1)
                            AS threshold_exceedance_pct
                    FROM hourly
                    WHERE start_count > 4
                    ORDER BY start_count DESC, asset_id
                    """,
                )
            )
            for event_type, predicate in (
                ("flow_proof_loss", "enable_cmd = 1 AND flow_proof = 0"),
                ("defrost", "defrost_cmd = 1"),
            ):
                rows.extend(
                    self._query(
                        connection,
                        f"""
                        WITH matching AS (
                            SELECT *
                            FROM telemetry_clean
                            WHERE {predicate}
                        ), boundaries AS (
                            SELECT
                                *,
                                CASE
                                    WHEN date_diff(
                                        'second',
                                        lag(timestamp) OVER (
                                            PARTITION BY asset_id ORDER BY timestamp
                                        ),
                                        timestamp
                                    ) = {self.sample_seconds}
                                    THEN 0 ELSE 1
                                END AS new_group
                            FROM matching
                        ), grouped AS (
                            SELECT
                                *,
                                sum(new_group) OVER (
                                    PARTITION BY asset_id ORDER BY timestamp
                                ) AS group_id
                            FROM boundaries
                        )
                        SELECT
                            '{event_type}' AS event_type,
                            asset_id,
                            CAST(min(timestamp) AS VARCHAR) AS start_time,
                            CAST(
                                max(timestamp)
                                + INTERVAL {self.sample_seconds} SECOND
                                AS VARCHAR
                            ) AS end_time,
                            count(*) AS sample_count,
                            count(*) * {self.sample_seconds} AS duration_seconds,
                            round(min(compressor_cmd_hz), 6)
                                AS min_compressor_cmd_hz,
                            round(min(compressor_fb_hz), 6)
                                AS min_compressor_fb_hz,
                            round(max(outdoor_fan_fb_pct), 6)
                                AS max_outdoor_fan_fb_pct,
                            round(
                                arg_max(outdoor_coil_temp_c, timestamp)
                                - arg_min(outdoor_coil_temp_c, timestamp),
                                6
                            ) AS outdoor_coil_temp_rise_c,
                            max(alarm_code) AS alarm_code
                        FROM grouped
                        GROUP BY asset_id, group_id
                        ORDER BY start_time, asset_id
                        """,
                    )
                )
        finally:
            connection.close()
        return SnapshotInspectionResult(
            operation="control_events",
            title="控制命令、反馈与状态事件",
            summary=(
                "已按当前项目测试口径盘点压缩机、电子膨胀阀和风机反馈偏差，"
                "并汇总频繁启停、流量证明丢失和除霜事件。"
            ),
            rows=rows,
        )

    def _alarm_events(self) -> SnapshotInspectionResult:
        connection = self._connect()
        try:
            rows = self._query(
                connection,
                f"""
                WITH matching AS (
                    SELECT *
                    FROM telemetry_clean
                    WHERE alarm_active = 1 AND alarm_code IS NOT NULL
                ), boundaries AS (
                    SELECT
                        *,
                        CASE
                            WHEN date_diff(
                                'second',
                                lag(timestamp) OVER (
                                    PARTITION BY asset_id, alarm_code
                                    ORDER BY timestamp
                                ),
                                timestamp
                            ) = {self.sample_seconds}
                            THEN 0 ELSE 1
                        END AS new_group
                    FROM matching
                ), grouped AS (
                    SELECT
                        *,
                        sum(new_group) OVER (
                            PARTITION BY asset_id, alarm_code
                            ORDER BY timestamp
                        ) AS group_id
                    FROM boundaries
                )
                SELECT
                    'alarm' AS event_type,
                    asset_id,
                    alarm_code,
                    CAST(min(timestamp) AS VARCHAR) AS start_time,
                    CAST(
                        max(timestamp) + INTERVAL {self.sample_seconds} SECOND
                        AS VARCHAR
                    ) AS end_time,
                    count(*) AS sample_count,
                    count(*) * {self.sample_seconds} AS duration_seconds,
                    round(max(discharge_temp_c), 6) AS max_discharge_temp_c,
                    round(avg(outdoor_fan_cmd_pct), 6)
                        AS average_outdoor_fan_cmd_pct,
                    round(avg(outdoor_fan_fb_pct), 6)
                        AS average_outdoor_fan_fb_pct,
                    round(avg(compressor_cmd_hz), 6)
                        AS average_compressor_cmd_hz,
                    round(avg(compressor_fb_hz), 6)
                        AS average_compressor_fb_hz
                FROM grouped
                GROUP BY asset_id, alarm_code, group_id
                ORDER BY start_time, asset_id
                """,
            )
        finally:
            connection.close()
        return SnapshotInspectionResult(
            operation="alarm_events",
            title="报警事件与现场观测",
            summary=(
                "已按机组和报警代码汇总连续报警窗口、持续时间以及关键命令和反馈。"
            ),
            rows=rows,
        )

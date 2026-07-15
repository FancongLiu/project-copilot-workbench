from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from project_copilot.platform_compat import ensure_windows_architecture_env


ensure_windows_architecture_env()

import duckdb  # noqa: E402
import pandera.polars as pa  # noqa: E402
import polars as pl  # noqa: E402
from filelock import FileLock, Timeout as FileLockTimeout  # noqa: E402

from project_copilot.sql_guard import SQLSelectGuard  # noqa: E402


class AnalyticsValidationError(ValueError):
    """Raised when imported telemetry violates the approved schema."""


@dataclass(frozen=True)
class MetricSnapshot:
    row_count: int
    average_power_kw: float
    average_delta_t_c: float
    average_cop: float


TELEMETRY_SCHEMA = pa.DataFrameSchema(
    {
        "timestamp": pa.Column(pl.Datetime),
        "supply_temp_c": pa.Column(float, checks=pa.Check.in_range(-30, 80)),
        "return_temp_c": pa.Column(float, checks=pa.Check.in_range(-30, 80)),
        "power_kw": pa.Column(float, checks=pa.Check.greater_than(0)),
        "cooling_kw": pa.Column(float, checks=pa.Check.greater_than_or_equal_to(0)),
        "load_pct": pa.Column(float, checks=pa.Check.in_range(0, 100)),
    },
    strict=True,
    coerce=True,
)


class AnalyticsWorkspace:
    ALLOWED_COLUMNS = {
        "timestamp",
        "supply_temp_c",
        "return_temp_c",
        "power_kw",
        "cooling_kw",
        "load_pct",
    }

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.resolve()
        self.guard = SQLSelectGuard(
            allowed_tables={"telemetry"},
            allowed_columns=self.ALLOWED_COLUMNS,
            max_rows=500,
        )

    @classmethod
    def build(
        cls, *, csv_path: str | Path, database_path: str | Path
    ) -> "AnalyticsWorkspace":
        source = Path(csv_path).resolve()
        target = Path(database_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            frame = pl.read_csv(source, try_parse_dates=True)
            frame = TELEMETRY_SCHEMA.validate(frame)
        except (OSError, pl.exceptions.PolarsError, pa.errors.SchemaError) as exc:
            raise AnalyticsValidationError(
                f"Telemetry schema validation failed: {exc}"
            ) from exc

        if frame.filter(pl.col("return_temp_c") < pl.col("supply_temp_c")).height:
            raise AnalyticsValidationError(
                "HVAC return temperature must not be below supply temperature"
            )

        curated_path: Path | None = None
        building_path: Path | None = None
        try:
            with FileLock(str(target) + ".lock", timeout=30):
                with NamedTemporaryFile(
                    mode="wb",
                    suffix=".curated.csv",
                    dir=target.parent,
                    delete=False,
                ) as temporary_file:
                    curated_path = Path(temporary_file.name)
                with NamedTemporaryFile(
                    mode="wb",
                    suffix=".building.duckdb",
                    dir=target.parent,
                    delete=False,
                ) as temporary_database:
                    building_path = Path(temporary_database.name)
                building_path.unlink()

                frame.write_csv(curated_path)
                connection = duckdb.connect(str(building_path))
                try:
                    connection.execute(
                        "CREATE TABLE telemetry AS SELECT * FROM read_csv_auto(?)",
                        [str(curated_path)],
                    )
                    connection.execute("CHECKPOINT")
                finally:
                    connection.close()
                os.replace(building_path, target)
        except (OSError, duckdb.Error, FileLockTimeout) as exc:
            raise AnalyticsValidationError(
                f"Telemetry snapshot build failed: {exc}"
            ) from exc
        finally:
            if curated_path is not None:
                curated_path.unlink(missing_ok=True)
            if building_path is not None:
                building_path.unlink(missing_ok=True)

        return cls(target)

    def _connect_read_only(self) -> duckdb.DuckDBPyConnection:
        connection = duckdb.connect(str(self.database_path), read_only=True)
        connection.execute("SET enable_external_access = false")
        connection.execute("SET autoinstall_known_extensions = false")
        connection.execute("SET autoload_known_extensions = false")
        connection.execute("SET allow_community_extensions = false")
        connection.execute("SET memory_limit = '256MB'")
        connection.execute("SET threads = 2")
        connection.execute("SET max_temp_directory_size = '0GB'")
        connection.execute("SET lock_configuration = true")
        return connection

    def query(self, sql: str) -> list[dict[str, Any]]:
        guarded = self.guard.validate(sql)
        connection = self._connect_read_only()
        try:
            cursor = connection.execute(guarded.sql)
            columns = [item[0] for item in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        finally:
            connection.close()

    def metric_snapshot(self) -> MetricSnapshot:
        row = self.query(
            """
            SELECT
                COUNT(*) AS row_count,
                AVG(power_kw) AS average_power_kw,
                AVG(return_temp_c - supply_temp_c) AS average_delta_t_c,
                AVG(cooling_kw / NULLIF(power_kw, 0)) AS average_cop
            FROM telemetry
            """
        )[0]
        return MetricSnapshot(
            row_count=int(row["row_count"]),
            average_power_kw=float(row["average_power_kw"]),
            average_delta_t_c=float(row["average_delta_t_c"]),
            average_cop=float(row["average_cop"]),
        )

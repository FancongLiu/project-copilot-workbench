import pytest

from project_copilot.sql_guard import SQLPolicyError, SQLSelectGuard


def test_sql_guard_allows_single_select_and_applies_row_limit() -> None:
    guard = SQLSelectGuard(allowed_tables={"telemetry"}, max_rows=200)

    guarded = guard.validate("select avg(power_kw) as avg_power from telemetry")

    assert guarded.tables == ("telemetry",)
    assert guarded.sql == "SELECT AVG(power_kw) AS avg_power FROM telemetry LIMIT 200"


@pytest.mark.parametrize(
    "sql",
    [
        "delete from telemetry",
        "select * from telemetry; drop table telemetry",
        "select * from read_csv_auto('private.csv')",
        "attach 'private.duckdb' as private",
        "pragma database_list",
    ],
)
def test_sql_guard_rejects_mutation_and_file_access(sql: str) -> None:
    guard = SQLSelectGuard(allowed_tables={"telemetry"})

    with pytest.raises(SQLPolicyError):
        guard.validate(sql)


def test_sql_guard_rejects_unknown_tables() -> None:
    guard = SQLSelectGuard(allowed_tables={"telemetry"})

    with pytest.raises(SQLPolicyError, match="not allowed"):
        guard.validate("select * from payroll")


@pytest.mark.parametrize(
    "sql",
    [
        "select sleep_ms(1000) from telemetry",
        "select repeat('x', 1000000) from telemetry",
        "select * from telemetry",
        "select power_kw from (select power_kw from telemetry)",
        "with source as (select power_kw from telemetry) select power_kw from source",
    ],
)
def test_sql_guard_rejects_unbounded_or_nested_queries(sql: str) -> None:
    guard = SQLSelectGuard(
        allowed_tables={"telemetry"},
        allowed_columns={"power_kw"},
    )

    with pytest.raises(SQLPolicyError):
        guard.validate(sql)


def test_sql_guard_rejects_unknown_columns() -> None:
    guard = SQLSelectGuard(
        allowed_tables={"telemetry"},
        allowed_columns={"power_kw"},
    )

    with pytest.raises(SQLPolicyError, match="Columns are not allowed"):
        guard.validate("select employee_name from telemetry")

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp, parse
from sqlglot.errors import ParseError


class SQLPolicyError(ValueError):
    """Raised when generated SQL violates the read-only policy."""


@dataclass(frozen=True)
class GuardedQuery:
    sql: str
    tables: tuple[str, ...]


class SQLSelectGuard:
    _allowed_function_types = (exp.Avg, exp.Cast, exp.Count, exp.Max, exp.Nullif)

    def __init__(
        self,
        *,
        allowed_tables: set[str],
        allowed_columns: set[str] | None = None,
        max_rows: int = 500,
    ) -> None:
        self.allowed_tables = {table.casefold() for table in allowed_tables}
        self.allowed_columns = (
            {column.casefold() for column in allowed_columns}
            if allowed_columns is not None
            else None
        )
        self.max_rows = max_rows

    def validate(self, sql: str) -> GuardedQuery:
        if len(sql) > 4_000:
            raise SQLPolicyError("SQL exceeds the maximum permitted length")
        try:
            statements = parse(sql, read="duckdb")
        except ParseError as exc:
            raise SQLPolicyError(f"SQL could not be parsed: {exc}") from exc

        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            raise SQLPolicyError("Only one SELECT statement is allowed")

        statement = statements[0]
        if len(list(statement.find_all(exp.Select))) != 1:
            raise SQLPolicyError("Nested queries and CTEs are not allowed")
        if statement.args.get("with_") is not None or statement.find(exp.Subquery):
            raise SQLPolicyError("Nested queries and CTEs are not allowed")

        unsupported_functions = {
            type(node).__name__
            for node in statement.find_all(exp.Func)
            if not isinstance(node, self._allowed_function_types)
        }
        if unsupported_functions:
            raise SQLPolicyError(
                f"Functions are not allowed: {sorted(unsupported_functions)}"
            )

        unsafe_stars = [
            star
            for star in statement.find_all(exp.Star)
            if not isinstance(star.parent, exp.Count)
        ]
        if unsafe_stars:
            raise SQLPolicyError("Wildcard column selection is not allowed")

        tables = tuple(
            sorted(
                {
                    table.name.casefold()
                    for table in statement.find_all(exp.Table)
                    if table.name
                }
            )
        )
        unknown_tables = sorted(set(tables) - self.allowed_tables)
        if unknown_tables:
            raise SQLPolicyError(f"Tables are not allowed: {unknown_tables}")

        if self.allowed_columns is not None:
            columns = {
                column.name.casefold()
                for column in statement.find_all(exp.Column)
                if column.name
            }
            unknown_columns = sorted(columns - self.allowed_columns)
            if unknown_columns:
                raise SQLPolicyError(f"Columns are not allowed: {unknown_columns}")

        existing_limit = statement.args.get("limit")
        if existing_limit is None:
            statement = statement.limit(self.max_rows)
        else:
            limit_value = existing_limit.expression
            if isinstance(limit_value, exp.Literal) and limit_value.is_int:
                if int(limit_value.this) > self.max_rows:
                    statement.set(
                        "limit", exp.Limit(expression=exp.Literal.number(self.max_rows))
                    )
            else:
                raise SQLPolicyError("LIMIT must be a fixed integer")

        return GuardedQuery(sql=statement.sql(dialect="duckdb"), tables=tables)

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
    _allowed_function_types = (
        exp.Avg,
        exp.And,
        exp.Case,
        exp.Cast,
        exp.Count,
        exp.If,
        exp.Max,
        exp.Min,
        exp.Nullif,
        exp.Round,
        exp.Sum,
        exp.TimestampTrunc,
    )

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

        table_nodes = list(statement.find_all(exp.Table))
        if len(table_nodes) != 1 or statement.args.get("joins"):
            raise SQLPolicyError(
                "Query must read exactly one approved table without joins"
            )
        tables = tuple(
            sorted({table.name.casefold() for table in table_nodes if table.name})
        )
        unknown_tables = sorted(set(tables) - self.allowed_tables)
        if unknown_tables:
            raise SQLPolicyError(f"Tables are not allowed: {unknown_tables}")

        for projection in statement.expressions:
            expression = (
                projection.this if isinstance(projection, exp.Alias) else projection
            )
            if isinstance(expression, exp.Count) and expression.find(exp.Star):
                continue
            if not isinstance(expression, exp.Column) and not list(
                expression.find_all(exp.Column)
            ):
                raise SQLPolicyError(
                    "Every selected value must derive from an approved column"
                )

        if self.allowed_columns is not None:
            projection_aliases = {
                alias.alias.casefold()
                for alias in statement.expressions
                if isinstance(alias, exp.Alias) and alias.alias
            }
            columns = {
                column.name.casefold()
                for column in statement.find_all(exp.Column)
                if column.name
            }
            unknown_columns = sorted(
                columns - self.allowed_columns - projection_aliases
            )
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

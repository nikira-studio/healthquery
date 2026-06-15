from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp


@dataclass(frozen=True)
class SqlGuardResult:
    sql: str
    normalized_sql: str


class SqlGuardError(ValueError):
    pass


def validate_read_only_sql(sql: str) -> SqlGuardResult:
    statement = sql.strip().rstrip(";")
    if not statement:
        raise SqlGuardError("SQL query is required")

    if ";" in statement:
        raise SqlGuardError("Only one statement is allowed")

    try:
        parsed = sqlglot.parse_one(statement, read="sqlite")
    except Exception as exc:
        raise SqlGuardError(f"Invalid SQL: {exc}") from exc

    if not isinstance(parsed, exp.Select):
        raise SqlGuardError("Only SELECT statements are allowed")

    forbidden_nodes = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.TruncateTable,
        exp.Attach,
        exp.Pragma,
        exp.Command,
    )
    for node_type in forbidden_nodes:
        if list(parsed.find_all(node_type)):
            raise SqlGuardError(f"Forbidden SQL construct: {node_type.__name__}")

    normalized_sql = parsed.sql(dialect="sqlite")
    return SqlGuardResult(sql=statement, normalized_sql=normalized_sql)

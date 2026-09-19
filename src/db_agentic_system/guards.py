from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from db_agentic_system.config import DatabaseConfig
from db_agentic_system.policy import is_sensitive_column_name, is_sensitive_question
from db_agentic_system.scope import RecordScope, scope_violation


BLOCKED_SQL_PATTERNS = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|merge|call|execute)\b",
    re.IGNORECASE,
)

def check_question_policy(question: str) -> tuple[bool, str | None]:
    if is_sensitive_question(question):
        return False, "The question asks for sensitive or blocked information."
    return True, None


def validate_sql(
    sql: str,
    db_config: DatabaseConfig,
    max_rows: int,
    record_scope: RecordScope | None = None,
) -> tuple[bool, str | None, str]:
    cleaned = sql.strip().rstrip(";")
    if not cleaned:
        return False, "SQL is empty.", cleaned

    if BLOCKED_SQL_PATTERNS.search(cleaned):
        return False, "Only read-only SELECT queries are allowed.", cleaned

    try:
        parsed = sqlglot.parse_one(cleaned, read=db_config.dialect)
    except sqlglot.errors.ParseError as exc:
        return False, f"SQL parse failed: {exc}", cleaned

    if not _is_readonly_query(parsed):
        return False, "Only SELECT-style queries are allowed.", cleaned

    blocked_columns = {column.lower() for column in db_config.blocked_columns}
    selected_columns = {column.name.lower() for column in parsed.find_all(exp.Column)}
    if db_config.auto_block_sensitive_columns:
        blocked_columns.update(
            column for column in selected_columns if is_sensitive_column_name(column)
        )
    forbidden = sorted(selected_columns & blocked_columns)
    if forbidden:
        return False, f"Query selects blocked columns: {', '.join(forbidden)}.", cleaned

    used_tables = {table.name.lower() for table in parsed.find_all(exp.Table)}

    blocked_tables = {table.lower() for table in db_config.blocked_tables}
    forbidden_tables = sorted(used_tables & blocked_tables)
    if forbidden_tables:
        return False, "Query uses a table blocked by access policy.", cleaned

    if db_config.include_tables:
        allowed_tables = {table.lower() for table in db_config.include_tables}
        disallowed = sorted(used_tables - allowed_tables)
        if disallowed:
            return False, f"Query uses disallowed tables: {', '.join(disallowed)}.", cleaned

    # An investigation pinned to one record only stays pinned if the binding is
    # enforced here, alongside the read-only guarantees.
    if record_scope is not None:
        violation = scope_violation(parsed, record_scope)
        if violation:
            return False, violation, cleaned

    limited = _ensure_limit(cleaned, parsed, max_rows, db_config.dialect)
    return True, None, limited


def _is_readonly_query(parsed: exp.Expression) -> bool:
    return isinstance(parsed, exp.Select | exp.Union | exp.Subquery)


def _ensure_limit(sql: str, parsed: exp.Expression, max_rows: int, dialect: str) -> str:
    if isinstance(parsed, exp.Select) and parsed.args.get("limit") is None:
        return parsed.limit(max_rows).sql(dialect=dialect)
    return sql

"""Record scoping for the operations screen.

An ops investigation is pinned to one record: the operator picks loan 42 and
every query the agent runs has to stay on loan 42. Telling the model that in a
prompt is not enough — a scope that is only asked for is not a scope. So the
binding is also enforced at the same place the read-only guardrails are: a
query that does not tie itself to the record never reaches the database.

Bindings are table-aware, because a column name alone does not identify a
record: ``id = 42`` means loan 42 on ``m_loan`` and client 42 on ``m_client``.
A key binding therefore names its table, while a foreign key that carries the
record's id under its own name (``loan_id``) holds on any table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlglot import exp

from db_agentic_system.config import OpsConfig


# Identifiers come from config rather than user input, but they are interpolated
# into SQL, so they are checked before use rather than trusted.
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def safe_identifier(name: str) -> str:
    if not IDENTIFIER.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


@dataclass(frozen=True)
class RecordScope:
    """One record an investigation is pinned to, and what binds a query to it."""

    database_id: str
    table: str
    key_column: str
    key_value: Any
    label: str
    # Columns that carry this record's id whatever table they sit on, e.g.
    # {"loan_id": "42"}.
    bindings: dict[str, str] = field(default_factory=dict)
    # Bindings that only hold on one table, e.g. {"m_loan": {"id": "42"}}.
    table_bindings: dict[str, dict[str, str]] = field(default_factory=dict)
    reference_tables: frozenset[str] = field(default_factory=frozenset)

    def binding_columns(self) -> list[str]:
        """Every column that can bind a query to this record, for display."""
        columns = set(self.bindings)
        for bound in self.table_bindings.values():
            columns.update(bound)
        return sorted(columns)

    def describe(self) -> str:
        """The scope as the SQL planner is told about it."""
        clauses = [f"{table}.{column} = {value}"
                   for table, bound in sorted(self.table_bindings.items())
                   for column, value in sorted(bound.items())]
        clauses += [f"{column} = {value}" for column, value in sorted(self.bindings.items())]
        return (
            f"This investigation is scoped to a single record: {self.label} "
            f"({self.table}.{self.key_column} = {self.key_value}). "
            f"Every query must be filtered to that record with one of: {', '.join(clauses)}. "
            "Never query the whole table or another record."
        )


def build_record_scope(ops: OpsConfig, record: dict[str, Any]) -> RecordScope:
    """Build the scope for a record row the operator selected."""
    key_value = record.get(ops.key_column)
    if key_value is None:
        raise ValueError(f"record has no {ops.key_column!r} value to scope to")

    # The record's own key only identifies it on its own table.
    table_bindings: dict[str, dict[str, str]] = {
        ops.table.lower(): {ops.key_column.lower(): str(key_value)}
    }
    # A foreign key named after the record holds wherever it appears.
    bindings = {column.lower(): str(key_value) for column in ops.alias_columns}

    # A related column points at another record this one belongs to, e.g. the
    # loan's client. It binds under its own name anywhere, and under the related
    # table's key column on that table.
    for column, target in ops.related_columns.items():
        value = record.get(column)
        # An absent related value must not become a free pass to every row.
        if value is None:
            continue
        bindings[column.lower()] = str(value)
        table_name, _, key_column = target.partition(".")
        table_bindings.setdefault(table_name.lower(), {})[
            (key_column or ops.key_column).lower()
        ] = str(value)

    return RecordScope(
        database_id=ops.database_id,
        table=ops.table,
        key_column=ops.key_column,
        key_value=key_value,
        label=record_label(ops, record),
        bindings=bindings,
        table_bindings=table_bindings,
        reference_tables=frozenset(table.lower() for table in ops.reference_tables),
    )


def record_label(ops: OpsConfig, record: dict[str, Any]) -> str:
    parts = [str(record[column]) for column in ops.label_columns if record.get(column) is not None]
    if not parts:
        return f"{ops.table} {record.get(ops.key_column)}"
    return " · ".join(parts)


def scope_violation(parsed: exp.Expression, scope: RecordScope) -> str | None:
    """Return why a parsed query escapes the record scope, or None if it stays inside.

    A query is inside the scope when it compares one of the scope's columns to
    that record's value, unconditionally. A comparison that sits under an OR or a
    NOT is not a filter — `loan_id = 42 OR 1 = 1` reads the whole table — so those
    do not count.
    """
    used_tables = {table.name.lower() for table in parsed.find_all(exp.Table)}
    # A pure lookup against reference tables carries no record data, so it needs
    # no binding.
    if used_tables and used_tables <= scope.reference_tables:
        return None

    sources = _table_sources(parsed)
    for equality in parsed.find_all(exp.EQ):
        column, literal = _column_and_literal(equality)
        if column is None or literal is None:
            continue
        if not _binds_record(column, literal, scope, sources):
            continue
        if _is_conditional(equality):
            continue
        return None

    return (
        f"Query is not scoped to {scope.label}. Filter it with one of: "
        f"{', '.join(scope.binding_columns())} = {scope.key_value}."
    )


def _binds_record(
    column: exp.Column,
    literal: exp.Literal,
    scope: RecordScope,
    sources: dict[str, str],
) -> bool:
    name = column.name.lower()

    expected = scope.bindings.get(name)
    if expected is not None and _literal_matches(literal, expected):
        return True

    # A key column has to be read against the table it belongs to, so that
    # `m_client.id = 7` binds the loan's client while `m_client.id = 42` does not.
    for table in _candidate_tables(column, sources):
        expected = scope.table_bindings.get(table, {}).get(name)
        if expected is not None and _literal_matches(literal, expected):
            return True
    return False


def _candidate_tables(column: exp.Column, sources: dict[str, str]) -> list[str]:
    """The tables a column could belong to, resolving any alias it is qualified with."""
    qualifier = (column.table or "").lower()
    if qualifier:
        return [sources.get(qualifier, qualifier)]
    # Unqualified in a single-table query is unambiguous; in a join it is
    # ambiguous SQL, so every source is a candidate.
    return sorted(set(sources.values()))


def _table_sources(parsed: exp.Expression) -> dict[str, str]:
    """Map each alias and table name in the query to the real table name."""
    sources: dict[str, str] = {}
    for table in parsed.find_all(exp.Table):
        name = table.name.lower()
        sources[name] = name
        if table.alias:
            sources[table.alias.lower()] = name
    return sources


def _column_and_literal(
    equality: exp.EQ,
) -> tuple[exp.Column | None, exp.Literal | None]:
    left, right = equality.left, equality.right
    if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
        return left, right
    if isinstance(right, exp.Column) and isinstance(left, exp.Literal):
        return right, left
    return None, None


def _literal_matches(literal: exp.Literal, expected: str) -> bool:
    value = str(literal.this)
    if value == expected:
        return True
    # 42 and 42.0 are the same record key; compare numerically when both sides are.
    try:
        return float(value) == float(expected)
    except (TypeError, ValueError):
        return False


def _is_conditional(node: exp.Expression) -> bool:
    """True when this comparison is optional — under an OR, or negated."""
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Or | exp.Not):
            return True
        if isinstance(parent, exp.Select):
            return False
        parent = parent.parent
    return False

"""The operations investigation flow: pick a record, investigate it, get a brief.

The screen is deliberately narrow. An operator picks one record, the agent is
pinned to it (see :mod:`db_agentic_system.scope`), every query it runs goes
through the same read-only guardrails as the chat, and what comes back is a
brief whose every claim links to the query that produced it.
"""

from __future__ import annotations

import re
from typing import Any

from db_agentic_system.config import AgentConfig, OpsConfig
from db_agentic_system.database import DatabaseRegistry
from db_agentic_system.guards import validate_sql
from db_agentic_system.scope import RecordScope, safe_identifier

# How many rows of each query the brief keeps as shown evidence.
EVIDENCE_ROW_LIMIT = 10

CITATION = re.compile(r"\[(e\d+)\]")


class OpsError(RuntimeError):
    """A record-investigation request that cannot be served as asked."""


def ops_config(config: AgentConfig) -> OpsConfig:
    if config.ops is None:
        raise OpsError("This profile has no ops section, so it has no record to investigate.")
    return config.ops


def _picker_columns(ops: OpsConfig) -> list[str]:
    columns = [ops.key_column, *ops.label_columns, *ops.detail_columns, *ops.related_columns]
    # ops.related_columns is a mapping; iterating it yields the record's own columns.
    seen: list[str] = []
    for column in columns:
        safe_identifier(column)
        if column not in seen:
            seen.append(column)
    return seen


def list_records(
    registry: DatabaseRegistry,
    config: AgentConfig,
    search: str = "",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """List records for the picker, newest first, optionally filtered by a search term.

    The query is built here rather than by the model — the picker is a fixed
    lookup, not an investigation — but it still goes through ``validate_sql`` so
    one code path decides what may run. The search term is bound as a parameter,
    never interpolated.
    """
    ops = ops_config(config)
    table = safe_identifier(ops.table)
    key_column = safe_identifier(ops.key_column)
    columns = ", ".join(_picker_columns(ops))
    page_size = limit or ops.page_size

    term = search.strip()
    parameters: dict[str, Any] = {}
    where = ""
    if term and ops.search_columns:
        clauses = []
        for index, column in enumerate(ops.search_columns):
            name = f"search_{index}"
            clauses.append(f"CAST({safe_identifier(column)} AS TEXT) LIKE :{name}")
            parameters[name] = f"%{term}%"
        where = f" WHERE {' OR '.join(clauses)}"

    sql = f"SELECT {columns} FROM {table}{where} ORDER BY {key_column} DESC LIMIT {int(page_size)}"

    db_config = registry.get_config(ops.database_id)
    valid, reason, checked = validate_sql(sql, db_config, page_size)
    if not valid:
        raise OpsError(f"The record list is blocked by access policy: {reason}")

    return registry.execute_readonly(ops.database_id, checked, parameters)


def load_record(
    registry: DatabaseRegistry,
    config: AgentConfig,
    record_id: str,
) -> dict[str, Any]:
    """Load the one record an investigation will be pinned to."""
    ops = ops_config(config)
    table = safe_identifier(ops.table)
    key_column = safe_identifier(ops.key_column)
    columns = ", ".join(_picker_columns(ops))

    sql = f"SELECT {columns} FROM {table} WHERE {key_column} = :record_id LIMIT 1"
    db_config = registry.get_config(ops.database_id)
    valid, reason, checked = validate_sql(sql, db_config, 1)
    if not valid:
        raise OpsError(f"The record lookup is blocked by access policy: {reason}")

    rows = registry.execute_readonly(ops.database_id, checked, {"record_id": _coerce(record_id)})
    if not rows:
        raise OpsError(f"No {ops.table} record with {ops.key_column} {record_id!r}.")
    return rows[0]


def _coerce(record_id: str) -> Any:
    """Compare a key against the database as the number it is, when it is one."""
    try:
        return int(record_id)
    except (TypeError, ValueError):
        return record_id


def build_evidence(query_results: list[dict[str, Any]], plans: list[dict[str, str]]) -> list[dict]:
    """Number each executed query so the brief can cite it.

    Ids are ``e1``, ``e2``… in execution order. A plan that was blocked by the
    guardrails produced no rows, so it is not evidence and gets no id; it is
    reported separately.
    """
    purposes = {plan.get("sql"): plan.get("purpose", "") for plan in plans}
    evidence = []
    for index, result in enumerate(query_results, start=1):
        rows = result.get("rows", [])
        evidence.append(
            {
                "id": f"e{index}",
                "database_id": result.get("database_id"),
                "purpose": purposes.get(result.get("sql"), ""),
                "sql": result.get("sql"),
                "row_count": result.get("row_count", len(rows)),
                "rows": rows[:EVIDENCE_ROW_LIMIT],
                "truncated": len(rows) > EVIDENCE_ROW_LIMIT,
            }
        )
    return evidence


def brief_segments(text: str, evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Split brief text into plain text and citation segments the screen can link.

    A citation the evidence does not contain is dropped rather than rendered: a
    link that opens nothing is worse than no link, and a model citing ``[e9]``
    when there are three queries is exactly the claim an operator should not
    trust.
    """
    known = {item["id"] for item in evidence}
    segments: list[dict[str, str]] = []
    cursor = 0

    def add_text(chunk: str) -> None:
        if not chunk:
            return
        if segments and segments[-1]["type"] == "text":
            segments[-1]["text"] += chunk
        else:
            segments.append({"type": "text", "text": chunk})

    for match in CITATION.finditer(text):
        add_text(text[cursor : match.start()])
        cursor = match.end()
        if match.group(1) in known:
            segments.append({"type": "citation", "evidence_id": match.group(1)})
        # An unknown marker is removed along with any space it leaves behind.
    add_text(text[cursor:])
    return segments


def cited_evidence_ids(segments: list[dict[str, str]]) -> list[str]:
    seen: list[str] = []
    for segment in segments:
        if segment["type"] == "citation" and segment["evidence_id"] not in seen:
            seen.append(segment["evidence_id"])
    return seen


def blocked_plans(sql_plans: list[dict[str, str]]) -> int:
    return sum(1 for plan in sql_plans if plan.get("sql") == "[blocked by access policy]")


def record_summary(ops: OpsConfig, record: dict[str, Any], scope: RecordScope) -> dict[str, Any]:
    """The selected record as the screen shows it."""
    return {
        "id": record.get(ops.key_column),
        "label": scope.label,
        "table": ops.table,
        "fields": [
            {"name": column, "value": record.get(column)}
            for column in [*ops.label_columns, *ops.detail_columns]
            if column in record
        ],
        "scope_columns": scope.binding_columns(),
    }

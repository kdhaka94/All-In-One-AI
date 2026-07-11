from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any


PRIMARY_KEY_CANDIDATES = (
    "id",
    "person_id",
    "user_id",
    "customer_id",
    "member_id",
    "account_id",
    "order_id",
)

NAME_CANDIDATES = (
    "name",
    "full_name",
    "first_name",
    "title",
    "username",
    "email",
    "description",
)


def build_memory_context(
    conversation_history: list[dict[str, Any]],
    memory_artifacts: list[dict[str, Any]] | None,
) -> str:
    visible_turns = [
        {"role": turn.get("role"), "content": turn.get("content")}
        for turn in conversation_history[-10:]
        if turn.get("role") in {"user", "assistant"}
    ]
    artifacts = (memory_artifacts or [])[-8:]
    payload = {
        "recent_visible_turns": visible_turns,
        "recent_sql_results": [
            result
            for artifact in artifacts
            for result in artifact.get("sql_results", artifact.get("query_results", []))
        ][-12:],
        "recent_entities": [
            entity
            for artifact in artifacts
            for entity in artifact.get("entities", [])
        ][-100:],
        "recent_facts": [
            fact
            for artifact in artifacts
            for fact in artifact.get("facts", [])
        ][-80:],
        "recent_agent_artifacts": artifacts,
        "guidance": (
            "Use recent_entities, recent_sql_results, and recent_facts to resolve follow-ups. "
            "Prefer exact IDs and previous SQL result rows over names or natural-language "
            "summaries. Do not reinterpret failed empty queries as facts."
        ),
    }
    return json.dumps(payload, default=str, indent=2)


def make_memory_artifact(
    user_message: str,
    result: dict[str, Any],
    summarized_results: list[dict[str, Any]],
) -> dict[str, Any]:
    turn_id = new_id("turn")
    query_results = result.get("query_results", [])
    sql_plans = result.get("sql_plans", [])
    entities = []
    sql_results = []
    for index, query_result in enumerate(query_results):
        plan = sql_plans[index] if index < len(sql_plans) else {}
        sql_result = make_sql_result(turn_id, query_result, plan)
        sql_results.append(sql_result)
        entities.extend(extract_entities_from_sql_result(turn_id, sql_result))

    return {
        "turn_id": turn_id,
        "created_at": now_iso(),
        "user_message": user_message,
        "standalone_question": result.get("question", user_message),
        "answer": result.get("answer", ""),
        "selected_databases": result.get("selected_databases", []),
        "sql_plans": result.get("sql_plans", []),
        "sql_results": sql_results,
        "query_results": summarized_results,
        "entities": entities[:120],
        "facts": extract_facts(result, sql_results),
    }


def make_sql_result(
    turn_id: str,
    query_result: dict[str, Any],
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = query_result.get("rows", [])
    columns = list(rows[0].keys()) if rows else []
    return {
        "id": new_id("sqlres"),
        "turn_id": turn_id,
        "database_id": query_result.get("database_id"),
        "purpose": (plan or {}).get("purpose", ""),
        "sql": query_result.get("sql", ""),
        "table_name": infer_main_table_from_sql(query_result.get("sql", "")),
        "columns": columns,
        "rows": rows[:100],
        "row_count": query_result.get("row_count", len(rows)),
        "created_at": now_iso(),
    }


def extract_entities_from_sql_result(turn_id: str, sql_result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = sql_result.get("rows", [])
    if not rows:
        return []

    database_id = sql_result.get("database_id")
    table_name = sql_result.get("table_name")
    columns = sql_result.get("columns") or list(rows[0].keys())
    primary_key_column = guess_primary_key_column(columns)
    name_column = guess_name_column(columns)
    if not primary_key_column:
        return []

    entities = []
    for row in rows[:100]:
        primary_key_value = row.get(primary_key_column)
        if primary_key_value is None:
            continue
        entities.append(
            {
                "id": new_id("ent"),
                "turn_id": turn_id,
                "entity_type": table_name or "row",
                "database_id": database_id,
                "table_name": table_name,
                "primary_key_column": primary_key_column,
                "primary_key_value": str(primary_key_value),
                "display_name": str(row.get(name_column, "")) if name_column else "",
                "evidence": row,
                "confidence": 0.85,
                "created_at": now_iso(),
            }
        )
    return entities


def extract_facts(result: dict[str, Any], sql_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts = []
    for sql_result in sql_results:
        row_count = sql_result.get("row_count", 0)
        if row_count:
            facts.append(
                {
                    "id": new_id("fact"),
                    "turn_id": sql_result.get("turn_id"),
                    "fact_type": "sql_result_available",
                    "fact_text": (
                        f"Query for {sql_result.get('purpose') or 'the previous question'} "
                        f"returned {row_count} row(s)."
                    ),
                    "data": {
                        "sql_result_id": sql_result.get("id"),
                        "database_id": sql_result.get("database_id"),
                        "table_name": sql_result.get("table_name"),
                        "row_count": row_count,
                    },
                    "created_at": now_iso(),
                }
            )
    if result.get("answer"):
        facts.append(
            {
                "id": new_id("fact"),
                "turn_id": sql_results[0]["turn_id"] if sql_results else None,
                "fact_type": "last_answer",
                "fact_text": result["answer"],
                "data": {},
                "created_at": now_iso(),
            }
        )
    return facts[:40]


def guess_primary_key_column(columns: list[str]) -> str | None:
    normalized = {column.lower(): column for column in columns}
    for candidate in PRIMARY_KEY_CANDIDATES:
        if candidate in normalized:
            return normalized[candidate]

    for column in columns:
        if re.search(r"(^id$|_id$)", column, re.IGNORECASE):
            return column
    return None


def guess_name_column(columns: list[str]) -> str | None:
    normalized = {column.lower(): column for column in columns}
    for candidate in NAME_CANDIDATES:
        if candidate in normalized:
            return normalized[candidate]
    return None


def infer_main_table_from_sql(sql: str) -> str | None:
    match = re.search(r"\bfrom\s+([`\"\[]?)([a-zA-Z_][\w.]*)\1", sql, re.IGNORECASE)
    if not match:
        return None
    return match.group(2).split(".")[-1]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()

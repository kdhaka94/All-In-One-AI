from __future__ import annotations

from typing import Any, TypedDict


class ConversationTurn(TypedDict):
    role: str
    content: str


class SelectedDatabase(TypedDict):
    id: str
    name: str
    reason: str
    score: float


class SqlPlan(TypedDict):
    database_id: str
    sql: str
    purpose: str


class QueryResult(TypedDict):
    database_id: str
    sql: str
    rows: list[dict[str, Any]]
    row_count: int


class AgentState(TypedDict, total=False):
    question: str
    original_question: str
    conversation_history: list[ConversationTurn]
    memory_context: str
    allowed: bool
    rejection_reason: str
    forced_database_ids: list[str]
    selected_databases: list[SelectedDatabase]
    selected_tables: dict[str, list[str]]
    schema_context: dict[str, str]
    sql_plans: list[SqlPlan]
    validation_errors: list[str]
    execution_errors: list[str]
    query_results: list[QueryResult]
    answer: str

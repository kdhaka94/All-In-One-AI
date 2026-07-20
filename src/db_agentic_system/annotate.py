from __future__ import annotations

import json

from langchain_core.language_models.chat_models import BaseChatModel

from db_agentic_system.catalog import DatabaseCatalog, TableProfile

_PROMPT = (
    "You document database schemas. Given a table, return STRICT JSON: "
    '{{"description": "<one sentence on what the table stores>", '
    '"columns": {{"<column>": "<short meaning>"}}}}. '
    "Only include columns whose meaning is not obvious from the name. "
    "Database: {db}. Table: {table}.\nColumns:\n{columns}\nForeign keys:\n{fks}\n"
    "Sample rows:\n{samples}"
)


def _table_prompt(db_name: str, table: TableProfile) -> str:
    columns = "\n".join(f"- {c.name} ({c.type})" for c in table.columns)
    fks = "\n".join(
        f"- {', '.join(fk.columns)} -> {fk.referred_table}" for fk in table.foreign_keys
    ) or "(none)"
    samples = json.dumps(table.sample_rows[:2], default=str) if table.sample_rows else "(none)"
    return _PROMPT.format(db=db_name, table=table.name, columns=columns, fks=fks, samples=samples)


def _parse(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}


def annotate_catalog(catalog: DatabaseCatalog, llm: BaseChatModel) -> DatabaseCatalog:
    result = catalog.model_copy(deep=True)
    for profile in result.databases:
        for table in profile.tables:
            if table.description:
                continue
            payload = _parse(llm.invoke(_table_prompt(profile.name, table)).content)
            if payload.get("description"):
                table.description = payload["description"]
            column_texts = payload.get("columns", {}) or {}
            for column in table.columns:
                text = column_texts.get(column.name)
                if text and not column.description:
                    column.description = text
    return result

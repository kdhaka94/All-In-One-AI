from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from db_agentic_system.catalog import DatabaseCatalog, catalog_schema_context
from db_agentic_system.config import AgentConfig, DatabaseConfig
from db_agentic_system.policy import is_sensitive_column_name


class DatabaseRegistry:
    def __init__(self, config: AgentConfig):
        self.config = config
        self._configs = {db.id: db for db in config.databases}
        self._engines: dict[str, Engine] = {}

    def all_configs(self) -> list[DatabaseConfig]:
        return list(self._configs.values())

    def get_config(self, database_id: str) -> DatabaseConfig:
        return self._configs[database_id]

    def get_engine(self, database_id: str) -> Engine:
        if database_id not in self._engines:
            self._engines[database_id] = create_engine(self.get_config(database_id).resolved_uri)
        return self._engines[database_id]

    def schema_context(
        self,
        database_ids: Iterable[str],
        catalog: DatabaseCatalog | None = None,
        source: str = "runtime",
        tables_by_db: dict[str, list[str]] | None = None,
    ) -> dict[str, str]:
        profiles = catalog.by_id() if catalog else {}
        context = {}
        for database_id in database_ids:
            allowed = None
            if tables_by_db and database_id in tables_by_db:
                allowed = set(tables_by_db[database_id])
            if source == "learned" and database_id in profiles:
                context[database_id] = catalog_schema_context(profiles[database_id], allowed)
            else:
                context[database_id] = self._schema_for_database(database_id, allowed)
        return context

    def execute_readonly(self, database_id: str, sql: str) -> list[dict[str, Any]]:
        engine = self.get_engine(database_id)
        with engine.connect() as connection:
            result = connection.execute(text(sql))
            return [dict(row._mapping) for row in result.fetchall()]

    def _schema_for_database(
        self, database_id: str, allowed_tables: set[str] | None = None
    ) -> str:
        db_config = self.get_config(database_id)
        engine = self.get_engine(database_id)
        inspector = inspect(engine)

        blocked_tables = {table.lower() for table in db_config.blocked_tables}
        allowed = (
            {name.lower() for name in allowed_tables} if allowed_tables is not None else None
        )
        table_names = [
            table
            for table in (db_config.include_tables or inspector.get_table_names())
            if table.lower() not in blocked_tables
            and (allowed is None or table.lower() in allowed)
        ]
        blocks = [
            f"Database id: {db_config.id}",
            f"Name: {db_config.name}",
            f"Description: {db_config.description}",
            f"Dialect: {db_config.dialect}",
            "Tables:",
        ]

        for table_name in table_names:
            columns = inspector.get_columns(table_name)
            safe_columns = [
                column
                for column in columns
                if column["name"].lower() not in {c.lower() for c in db_config.blocked_columns}
                and not (
                    db_config.auto_block_sensitive_columns
                    and is_sensitive_column_name(column["name"])
                )
            ]
            column_lines = [
                f"  - {column['name']} ({column['type']})" for column in safe_columns
            ]
            blocks.append(f"- {table_name}")
            blocks.extend(column_lines)

        if db_config.blocked_columns:
            blocks.append("Blocked columns must never be selected:")
            blocks.extend(f"- {column}" for column in db_config.blocked_columns)

        if db_config.blocked_tables:
            blocks.append("Some tables are hidden by access policy and must never be queried.")

        return "\n".join(blocks)

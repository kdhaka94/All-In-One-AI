from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, model_validator

load_dotenv()


class DatabaseConfig(BaseModel):
    id: str
    name: str
    description: str
    uri: str | None = None
    uri_env: str | None = None
    dialect: str = "sqlite"
    include_tables: list[str] = Field(default_factory=list)
    blocked_tables: list[str] = Field(default_factory=list)
    blocked_columns: list[str] = Field(default_factory=list)
    auto_block_sensitive_columns: bool = True
    sample_rows: int = 0

    @model_validator(mode="after")
    def require_uri_or_env(self) -> "DatabaseConfig":
        if not self.uri and not self.uri_env:
            raise ValueError(f"database {self.id!r} requires either uri or uri_env")
        return self

    @property
    def resolved_uri(self) -> str:
        if self.uri:
            return self.uri
        assert self.uri_env is not None
        value = os.getenv(self.uri_env)
        if not value:
            raise ValueError(f"environment variable {self.uri_env!r} is not set")
        return value


class OpsConfig(BaseModel):
    """Configures the operations screen: which record an investigation is scoped to.

    The screen is record-first: an operator picks one record (a loan account, say)
    and every query the agent runs must stay bound to it. That binding is expressed
    here in schema terms rather than hard-coded, so the same screen works for any
    core-banking schema.
    """

    database_id: str
    # The table the operator picks a record from, e.g. m_loan.
    table: str
    key_column: str = "id"
    # Columns shown for each record in the picker, first one as the headline.
    label_columns: list[str] = Field(default_factory=list)
    # Columns the picker's search box matches against.
    search_columns: list[str] = Field(default_factory=list)
    # Extra columns loaded for the selected record and shown on its card.
    detail_columns: list[str] = Field(default_factory=list)
    # Columns on OTHER tables that carry this record's key, e.g. m_loan_transaction.loan_id.
    alias_columns: list[str] = Field(default_factory=list)
    # Columns on the record's own row that point at another record, mapped to the
    # table they reference: {"client_id": "m_client"} or {"client_id": "m_client.id"}.
    related_columns: dict[str, str] = Field(default_factory=dict)
    # Lookup tables a query may read without a record binding, e.g. enum decode tables.
    reference_tables: list[str] = Field(default_factory=list)
    # The investigation the screen runs when the operator does not type their own.
    default_question: str = "Summarise this record and anything that needs attention."
    page_size: int = 25

    @model_validator(mode="after")
    def require_label_columns(self) -> "OpsConfig":
        if not self.label_columns:
            raise ValueError("ops.label_columns must list at least one column")
        return self


class AgentConfig(BaseModel):
    databases: list[DatabaseConfig]
    ops: OpsConfig | None = None
    max_selected_databases: int = 2
    min_route_score: float = 0.08
    max_rows: int = 100
    max_sql_iterations: int = 3
    catalog_path: str | None = None
    max_selected_tables: int | None = None
    annotations_path: str | None = None
    # Rank tables for selection with embeddings (default) or lexically. Lexical avoids
    # embedding every table per query — useful for very large schemas or limited embedding quota.
    table_selection_embeddings: bool = True

    @model_validator(mode="after")
    def require_unique_ids(self) -> "AgentConfig":
        ids = [db.id for db in self.databases]
        if len(ids) != len(set(ids)):
            raise ValueError("database ids must be unique")
        if self.ops and self.ops.database_id not in ids:
            raise ValueError(
                f"ops.database_id {self.ops.database_id!r} is not a configured database"
            )
        return self


def load_config(path: str | Path) -> AgentConfig:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
    try:
        return AgentConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid config {path}: {exc}") from exc

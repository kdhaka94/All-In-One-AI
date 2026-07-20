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


class AgentConfig(BaseModel):
    databases: list[DatabaseConfig]
    max_selected_databases: int = 2
    min_route_score: float = 0.08
    max_rows: int = 100
    max_sql_iterations: int = 3
    catalog_path: str | None = None
    max_selected_tables: int | None = None
    annotations_path: str | None = None

    @model_validator(mode="after")
    def require_unique_ids(self) -> "AgentConfig":
        ids = [db.id for db in self.databases]
        if len(ids) != len(set(ids)):
            raise ValueError("database ids must be unique")
        return self


def load_config(path: str | Path) -> AgentConfig:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
    try:
        return AgentConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid config {path}: {exc}") from exc

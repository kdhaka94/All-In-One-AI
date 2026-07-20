from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from db_agentic_system.config import AgentConfig, DatabaseConfig
from db_agentic_system.policy import is_sensitive_column_name


class ColumnProfile(BaseModel):
    name: str
    type: str
    nullable: bool | None = None
    primary_key: bool = False
    description: str | None = None
    enum_values: dict[str, str] | None = None


class ForeignKeyProfile(BaseModel):
    columns: list[str] = Field(default_factory=list)
    referred_table: str | None = None
    referred_columns: list[str] = Field(default_factory=list)


class TableProfile(BaseModel):
    name: str
    description: str | None = None
    columns: list[ColumnProfile] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyProfile] = Field(default_factory=list)
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)


class DatabaseProfile(BaseModel):
    id: str
    name: str
    description: str
    dialect: str
    learned_at: str
    include_tables: list[str] = Field(default_factory=list)
    blocked_tables: list[str] = Field(default_factory=list)
    blocked_columns: list[str] = Field(default_factory=list)
    auto_block_sensitive_columns: bool = True
    tables: list[TableProfile] = Field(default_factory=list)


class DatabaseCatalog(BaseModel):
    version: int = 1
    databases: list[DatabaseProfile] = Field(default_factory=list)

    def by_id(self) -> dict[str, DatabaseProfile]:
        return {database.id: database for database in self.databases}


def build_catalog(config: AgentConfig) -> DatabaseCatalog:
    profiles = []
    for db_config in config.databases:
        engine = create_engine(db_config.resolved_uri)
        profiles.append(profile_database(db_config, engine))
    return DatabaseCatalog(databases=profiles)


def profile_database(db_config: DatabaseConfig, engine: Engine) -> DatabaseProfile:
    inspector = inspect(engine)
    available_tables = inspector.get_table_names()
    blocked_tables = {table.lower() for table in db_config.blocked_tables}
    table_names = [
        table
        for table in (db_config.include_tables or available_tables)
        if table.lower() not in blocked_tables
    ]
    blocked_columns = {column.lower() for column in db_config.blocked_columns}

    tables = []
    for table_name in table_names:
        if table_name not in available_tables:
            continue

        primary_key = inspector.get_pk_constraint(table_name).get("constrained_columns", [])
        columns = []
        safe_column_names = []
        for column in inspector.get_columns(table_name):
            column_name = column["name"]
            if column_name.lower() in blocked_columns:
                continue
            if db_config.auto_block_sensitive_columns and is_sensitive_column_name(column_name):
                continue
            safe_column_names.append(column_name)
            columns.append(
                ColumnProfile(
                    name=column_name,
                    type=str(column["type"]),
                    nullable=column.get("nullable"),
                    primary_key=column_name in primary_key,
                )
            )

        foreign_keys = [
            ForeignKeyProfile(
                columns=foreign_key.get("constrained_columns") or [],
                referred_table=foreign_key.get("referred_table"),
                referred_columns=foreign_key.get("referred_columns") or [],
            )
            for foreign_key in inspector.get_foreign_keys(table_name)
        ]

        tables.append(
            TableProfile(
                name=table_name,
                columns=columns,
                foreign_keys=foreign_keys,
                sample_rows=_sample_rows(engine, table_name, safe_column_names, db_config.sample_rows),
            )
        )

    return DatabaseProfile(
        id=db_config.id,
        name=db_config.name,
        description=db_config.description,
        dialect=db_config.dialect,
        learned_at=datetime.now(UTC).isoformat(),
        include_tables=db_config.include_tables,
        blocked_tables=db_config.blocked_tables,
        blocked_columns=db_config.blocked_columns,
        auto_block_sensitive_columns=db_config.auto_block_sensitive_columns,
        tables=tables,
    )


def load_catalog(path: str | Path) -> DatabaseCatalog:
    return DatabaseCatalog.model_validate_json(Path(path).read_text())


def save_catalog(catalog: DatabaseCatalog, path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(catalog.model_dump_json(indent=2) + "\n")


def catalog_router_text(profile: DatabaseProfile) -> str:
    table_lines = []
    for table in profile.tables:
        column_names = ", ".join(column.name for column in table.columns)
        label = f" ({table.description})" if table.description else ""
        table_lines.append(f"{table.name}{label}: {column_names}")

    return "\n".join(
        [
            profile.id,
            profile.name,
            profile.description,
            f"dialect: {profile.dialect}",
            "tables and columns:",
            *table_lines,
        ]
    )


def catalog_schema_context(
    profile: DatabaseProfile, allowed_tables: set[str] | None = None
) -> str:
    allowed = (
        {name.lower() for name in allowed_tables} if allowed_tables is not None else None
    )
    tables = [t for t in profile.tables if allowed is None or t.name.lower() in allowed]

    blocks = [
        f"Database id: {profile.id}",
        f"Name: {profile.name}",
        f"Description: {profile.description}",
        f"Dialect: {profile.dialect}",
        f"Learned at: {profile.learned_at}",
        "Tables:",
    ]

    for table in tables:
        blocks.append(f"- {table.name}")
        if table.description:
            blocks.append(f"  purpose: {table.description}")
        for column in table.columns:
            primary_key = " primary_key" if column.primary_key else ""
            nullable = " nullable" if column.nullable else " not_null"
            description = f" — {column.description}" if column.description else ""
            blocks.append(f"  - {column.name} ({column.type}{primary_key}{nullable}){description}")
            if column.enum_values:
                legend = ", ".join(f"{code}={label}" for code, label in column.enum_values.items())
                blocks.append(f"    values: {legend}")
        for foreign_key in table.foreign_keys:
            if foreign_key.referred_table:
                blocks.append(
                    "  foreign key: "
                    f"{', '.join(foreign_key.columns)} -> "
                    f"{foreign_key.referred_table}({', '.join(foreign_key.referred_columns)})"
                )
        if table.sample_rows:
            blocks.append(f"  sample rows: {table.sample_rows}")

    if profile.blocked_columns:
        blocks.append("Blocked columns must never be selected:")
        blocks.extend(f"- {column}" for column in profile.blocked_columns)

    if profile.blocked_tables:
        blocks.append("Some tables are hidden by access policy and must never be queried.")

    if profile.auto_block_sensitive_columns:
        blocks.append("Common sensitive columns are automatically blocked by name.")

    return "\n".join(blocks)


def _sample_rows(
    engine: Engine,
    table_name: str,
    column_names: list[str],
    sample_rows: int,
) -> list[dict[str, Any]]:
    if sample_rows <= 0 or not column_names:
        return []

    preparer = engine.dialect.identifier_preparer
    quoted_table = preparer.quote(table_name)
    quoted_columns = ", ".join(preparer.quote(column_name) for column_name in column_names)
    sql = text(f"SELECT {quoted_columns} FROM {quoted_table} LIMIT {int(sample_rows)}")

    with engine.connect() as connection:
        result = connection.execute(sql)
        return [dict(row._mapping) for row in result.fetchall()]

# Fineract Full-Schema Example Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Apache Fineract locally (Dockerized, PostgreSQL), seed it with realistic banking data, and register it as a new read-only example in the multi-database agent that exposes Fineract's full ~300-table schema with per-question table-selection and a semantic annotation layer.

**Architecture:** Two independent halves. Phase A adds three generic, unit-tested agent capabilities (a table-selection pipeline stage, a description/enum annotation layer, and the config/state plumbing they need) — all TDD with injected fakes, no Docker. Phase B is Fineract infrastructure and data (Docker Compose + REST seed script + config/catalog/UI wiring), verified against the live stack. Phase A does not depend on Fineract; Phase B consumes Phase A.

**Tech Stack:** Python ≥3.11, pydantic v2, SQLAlchemy 2 + `psycopg`, sqlglot (dialect `postgres`), LangGraph, pytest, Docker Compose, Apache Fineract REST API.

## Global Constraints

- Python `>=3.11`; ruff `line-length = 100`.
- TDD: write the failing test first, watch it fail, implement minimally, watch it pass, commit.
- Tests use injected fakes only — **no network, no Docker, no live LLM** in `tests/`. Follow the existing `FakeChatModel(SimpleChatModel)` pattern in `tests/test_graph.py`.
- Pydantic models for structured data; `AgentState` is a `TypedDict(total=False)` in `src/db_agentic_system/state.py`.
- New annotation/table-selection fields are **optional** and default to today's behavior (unset ⇒ unchanged).
- sqlglot dialect string for Postgres is `"postgres"`.
- Fineract facts (verified from `apache/fineract` `develop`): images `apache/fineract` + `postgres`; Fineract listens on **8443 HTTPS** (self-signed); default tenant `default`; API base `https://localhost:8443/fineract-provider/api/v1`; admin `mifos` / `password`; tenant header `Fineract-Platform-TenantId: default`; master password `fineract`.
- Run all pytest with the project venv: `.venv/bin/pytest`. Run the CLI with `.venv/bin/db-agent`.

---

# Phase A — Agent core capabilities (no Docker, TDD)

### Task 1: Config fields for table-selection and annotations

**Files:**
- Modify: `src/db_agentic_system/config.py` (add two fields to `AgentConfig`)
- Test: `tests/test_config.py` (new)

**Interfaces:**
- Produces: `AgentConfig.max_selected_tables: int | None` (default `None`), `AgentConfig.annotations_path: str | None` (default `None`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from pathlib import Path

from db_agentic_system.config import load_config


def test_config_parses_table_selection_and_annotations_fields(tmp_path: Path) -> None:
    config_file = tmp_path / "cfg.yaml"
    config_file.write_text(
        """
max_selected_tables: 25
annotations_path: config/fineract_annotations.yaml
databases:
  - id: fineract
    name: Fineract
    description: Core banking.
    uri: postgresql+psycopg://u:p@localhost:5432/fineract_default
    dialect: postgres
    include_tables: []
"""
    )
    config = load_config(config_file)
    assert config.max_selected_tables == 25
    assert config.annotations_path == "config/fineract_annotations.yaml"
    assert config.databases[0].include_tables == []


def test_config_defaults_keep_today_behavior(tmp_path: Path) -> None:
    config_file = tmp_path / "cfg.yaml"
    config_file.write_text(
        """
databases:
  - id: x
    name: X
    description: d
    uri: sqlite:///x.db
"""
    )
    config = load_config(config_file)
    assert config.max_selected_tables is None
    assert config.annotations_path is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError: 'AgentConfig' object has no attribute 'max_selected_tables'`.

- [ ] **Step 3: Add the fields**

In `src/db_agentic_system/config.py`, inside `class AgentConfig`, add after `catalog_path`:

```python
    catalog_path: str | None = None
    max_selected_tables: int | None = None
    annotations_path: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/db_agentic_system/config.py tests/test_config.py
git commit -m "feat(config): add max_selected_tables and annotations_path"
```

---

### Task 2: Catalog description + enum fields and rendering

**Files:**
- Modify: `src/db_agentic_system/catalog.py` (`ColumnProfile`, `TableProfile`, `catalog_router_text`, `catalog_schema_context`)
- Test: `tests/test_catalog_render.py` (new)

**Interfaces:**
- Produces: `ColumnProfile.description: str | None`, `ColumnProfile.enum_values: dict[str, str] | None`, `TableProfile.description: str | None`. `catalog_schema_context(profile)` renders descriptions + enum legends. These field names are consumed by Tasks 3, 4, 7, 8.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog_render.py
from db_agentic_system.catalog import (
    ColumnProfile,
    DatabaseProfile,
    TableProfile,
    catalog_schema_context,
)


def _profile() -> DatabaseProfile:
    return DatabaseProfile(
        id="fineract",
        name="Fineract",
        description="Core banking.",
        dialect="postgres",
        learned_at="2026-07-21T00:00:00+00:00",
        tables=[
            TableProfile(
                name="m_loan",
                description="A loan account for a client.",
                columns=[
                    ColumnProfile(name="id", type="BIGINT", primary_key=True),
                    ColumnProfile(
                        name="loan_status_id",
                        type="SMALLINT",
                        description="Lifecycle status.",
                        enum_values={"100": "Submitted", "300": "Active", "600": "Closed"},
                    ),
                ],
            )
        ],
    )


def test_schema_context_renders_table_description_and_enum_legend() -> None:
    context = catalog_schema_context(_profile())
    assert "A loan account for a client." in context
    assert "Lifecycle status." in context
    assert "300=Active" in context
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_catalog_render.py -v`
Expected: FAIL — `TypeError` (unexpected keyword `description`) or assertion on missing enum text.

- [ ] **Step 3: Add fields and rendering**

In `src/db_agentic_system/catalog.py`, extend the models:

```python
class ColumnProfile(BaseModel):
    name: str
    type: str
    nullable: bool | None = None
    primary_key: bool = False
    description: str | None = None
    enum_values: dict[str, str] | None = None
```

```python
class TableProfile(BaseModel):
    name: str
    description: str | None = None
    columns: list[ColumnProfile] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyProfile] = Field(default_factory=list)
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
```

Replace the table/column loop inside `catalog_schema_context` (the `for table in profile.tables:` block) with:

```python
    for table in profile.tables:
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
```

In `catalog_router_text`, replace the `for table in profile.tables:` block with one that includes the description:

```python
    table_lines = []
    for table in profile.tables:
        column_names = ", ".join(column.name for column in table.columns)
        label = f" ({table.description})" if table.description else ""
        table_lines.append(f"{table.name}{label}: {column_names}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_catalog_render.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `.venv/bin/pytest -q`
Expected: all pass (existing catalogs simply have `description=None`).

- [ ] **Step 6: Commit**

```bash
git add src/db_agentic_system/catalog.py tests/test_catalog_render.py
git commit -m "feat(catalog): table/column descriptions and enum legends"
```

---

### Task 3: Annotation overlay (load + merge, user text wins)

**Files:**
- Create: `src/db_agentic_system/annotations.py`
- Test: `tests/test_annotations.py` (new)

**Interfaces:**
- Consumes: `DatabaseCatalog`, `DatabaseProfile`, `TableProfile`, `ColumnProfile` from `catalog.py` (Task 2).
- Produces: `load_annotations(path: str | Path) -> AnnotationOverlay`; `merge_annotations(catalog: DatabaseCatalog, overlay: AnnotationOverlay) -> DatabaseCatalog` (returns a new catalog; overlay descriptions override existing ones; matching is case-insensitive by table/column name). Consumed by Task 9.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_annotations.py
from db_agentic_system.annotations import AnnotationOverlay, load_annotations, merge_annotations
from db_agentic_system.catalog import ColumnProfile, DatabaseCatalog, DatabaseProfile, TableProfile


def _catalog() -> DatabaseCatalog:
    return DatabaseCatalog(
        databases=[
            DatabaseProfile(
                id="fineract",
                name="Fineract",
                description="Core banking.",
                dialect="postgres",
                learned_at="2026-07-21T00:00:00+00:00",
                tables=[
                    TableProfile(
                        name="m_loan",
                        description="AI: some loan table.",
                        columns=[ColumnProfile(name="loan_status_id", type="SMALLINT")],
                    )
                ],
            )
        ]
    )


def test_overlay_overrides_table_and_column_descriptions() -> None:
    overlay = AnnotationOverlay.model_validate(
        {
            "tables": {
                "m_loan": {
                    "description": "Human: the loan account.",
                    "columns": {"loan_status_id": "Human: loan lifecycle status."},
                }
            }
        }
    )
    merged = merge_annotations(_catalog(), overlay)
    table = merged.databases[0].tables[0]
    assert table.description == "Human: the loan account."
    assert table.columns[0].description == "Human: loan lifecycle status."


def test_load_annotations_reads_yaml(tmp_path) -> None:
    path = tmp_path / "ann.yaml"
    path.write_text(
        "tables:\n  m_loan:\n    description: The loan account.\n"
    )
    overlay = load_annotations(path)
    assert overlay.tables["m_loan"].description == "The loan account."


def test_merge_is_noop_for_unknown_tables() -> None:
    overlay = AnnotationOverlay.model_validate({"tables": {"nope": {"description": "x"}}})
    merged = merge_annotations(_catalog(), overlay)
    assert merged.databases[0].tables[0].description == "AI: some loan table."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_annotations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db_agentic_system.annotations'`.

- [ ] **Step 3: Implement the overlay module**

```python
# src/db_agentic_system/annotations.py
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from db_agentic_system.catalog import DatabaseCatalog


class TableAnnotation(BaseModel):
    description: str | None = None
    columns: dict[str, str] = Field(default_factory=dict)


class AnnotationOverlay(BaseModel):
    tables: dict[str, TableAnnotation] = Field(default_factory=dict)


def load_annotations(path: str | Path) -> AnnotationOverlay:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return AnnotationOverlay.model_validate(raw)


def merge_annotations(catalog: DatabaseCatalog, overlay: AnnotationOverlay) -> DatabaseCatalog:
    tables_by_name = {name.lower(): annotation for name, annotation in overlay.tables.items()}
    merged = catalog.model_copy(deep=True)
    for profile in merged.databases:
        for table in profile.tables:
            annotation = tables_by_name.get(table.name.lower())
            if annotation is None:
                continue
            if annotation.description:
                table.description = annotation.description
            columns_by_name = {c.lower(): text for c, text in annotation.columns.items()}
            for column in table.columns:
                text = columns_by_name.get(column.name.lower())
                if text:
                    column.description = text
    return merged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_annotations.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/db_agentic_system/annotations.py tests/test_annotations.py
git commit -m "feat(annotations): YAML description overlay with user-wins merge"
```

---

### Task 4: Table-selection module

**Files:**
- Create: `src/db_agentic_system/table_selection.py`
- Test: `tests/test_table_selection.py` (new)

**Interfaces:**
- Consumes: `DatabaseProfile`, `TableProfile` (Task 2); `Embedder` Protocol from `router.py`.
- Produces: `TableSelector(max_tables: int | None, embedder: Embedder | None = None, fk_neighbor_cap: int = 10)` with `.select(question: str, profile: DatabaseProfile) -> list[str]`. Returns all table names when `max_tables` is `None` or the profile has `<= max_tables` tables; otherwise top-K by score plus FK-referenced neighbors. Consumed by Task 6.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_table_selection.py
from db_agentic_system.catalog import ColumnProfile, DatabaseProfile, ForeignKeyProfile, TableProfile
from db_agentic_system.table_selection import TableSelector


class FakeEmbedder:
    """Deterministic embedder: vector = [count of query keywords present in the text]."""

    def __init__(self, keyword: str) -> None:
        self.keyword = keyword

    def embed_query(self, text: str) -> list[float]:
        return [1.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] if self.keyword in text else [0.0] for text in texts]


def _profile() -> DatabaseProfile:
    return DatabaseProfile(
        id="fineract",
        name="Fineract",
        description="Core banking.",
        dialect="postgres",
        learned_at="2026-07-21T00:00:00+00:00",
        tables=[
            TableProfile(
                name="m_loan",
                description="loan account",
                columns=[ColumnProfile(name="client_id", type="BIGINT")],
                foreign_keys=[
                    ForeignKeyProfile(
                        columns=["client_id"], referred_table="m_client", referred_columns=["id"]
                    )
                ],
            ),
            TableProfile(name="m_client", description="a client", columns=[]),
            TableProfile(name="m_office", description="an office", columns=[]),
        ],
    )


def test_returns_all_tables_when_under_limit() -> None:
    selector = TableSelector(max_tables=None)
    assert set(selector.select("loans", _profile())) == {"m_loan", "m_client", "m_office"}


def test_selects_top_k_plus_fk_neighbors() -> None:
    # keyword "loan" only matches m_loan's text; limit to 1 top table.
    selector = TableSelector(max_tables=1, embedder=FakeEmbedder("loan"))
    selected = selector.select("show me loans", _profile())
    assert "m_loan" in selected           # top-ranked
    assert "m_client" in selected         # pulled in as FK neighbor of m_loan
    assert "m_office" not in selected      # unrelated, excluded


def test_lexical_fallback_without_embedder() -> None:
    selector = TableSelector(max_tables=1)  # no embedder → lexical token overlap
    selected = selector.select("office", _profile())
    assert "m_office" in selected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_table_selection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db_agentic_system.table_selection'`.

- [ ] **Step 3: Implement the selector**

```python
# src/db_agentic_system/table_selection.py
from __future__ import annotations

import re
from dataclasses import dataclass

from db_agentic_system.catalog import DatabaseProfile, TableProfile
from db_agentic_system.router import Embedder, cosine_similarity

_VECTOR_CACHE: dict[tuple[str, str], tuple[list[str], list[list[float]]]] = {}


def _table_text(table: TableProfile) -> str:
    parts = [table.name]
    if table.description:
        parts.append(table.description)
    parts.append(" ".join(column.name for column in table.columns))
    return " ".join(parts)


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9_]+", text.lower()))


@dataclass
class TableSelector:
    max_tables: int | None
    embedder: Embedder | None = None
    fk_neighbor_cap: int = 10

    def select(self, question: str, profile: DatabaseProfile) -> list[str]:
        tables = profile.tables
        if self.max_tables is None or len(tables) <= self.max_tables:
            return [table.name for table in tables]

        scored = self._scores(question, profile)
        ranked = sorted(zip(scored, tables), key=lambda item: item[0], reverse=True)
        chosen = [table.name for _, table in ranked[: self.max_tables]]

        chosen_set = set(chosen)
        by_name = {table.name for table in tables}
        added = 0
        for name in list(chosen):
            table = next(table for table in tables if table.name == name)
            for foreign_key in table.foreign_keys:
                neighbor = foreign_key.referred_table
                if neighbor and neighbor in by_name and neighbor not in chosen_set:
                    chosen.append(neighbor)
                    chosen_set.add(neighbor)
                    added += 1
                    if added >= self.fk_neighbor_cap:
                        return chosen
        return chosen

    def _scores(self, question: str, profile: DatabaseProfile) -> list[float]:
        if self.embedder is not None:
            names, vectors = self._table_vectors(profile)
            question_vector = self.embedder.embed_query(question)
            return [cosine_similarity(question_vector, vector) for vector in vectors]
        query_terms = _tokenize(question)
        return [
            len(query_terms & _tokenize(_table_text(table))) / max(len(query_terms), 1)
            for table in profile.tables
        ]

    def _table_vectors(self, profile: DatabaseProfile) -> tuple[list[str], list[list[float]]]:
        assert self.embedder is not None
        key = (profile.id, profile.learned_at)
        cached = _VECTOR_CACHE.get(key)
        current_names = [table.name for table in profile.tables]
        if cached is not None and cached[0] == current_names:
            return cached
        vectors = self.embedder.embed_documents([_table_text(table) for table in profile.tables])
        _VECTOR_CACHE[key] = (current_names, vectors)
        return current_names, vectors
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_table_selection.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/db_agentic_system/table_selection.py tests/test_table_selection.py
git commit -m "feat(table-selection): rank tables with FK-neighbor expansion and vector cache"
```

---

### Task 5: Table allow-list in schema context

**Files:**
- Modify: `src/db_agentic_system/catalog.py` (`catalog_schema_context` signature)
- Modify: `src/db_agentic_system/database.py` (`schema_context`, `_schema_for_database`)
- Test: `tests/test_schema_allowlist.py` (new)

**Interfaces:**
- Produces: `catalog_schema_context(profile, allowed_tables: set[str] | None = None)` and `DatabaseRegistry.schema_context(database_ids, catalog=None, source="runtime", tables_by_db: dict[str, list[str]] | None = None)`. When an allow-list is given, only those tables render. Consumed by Task 6.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schema_allowlist.py
from db_agentic_system.catalog import ColumnProfile, DatabaseProfile, TableProfile, catalog_schema_context


def _profile() -> DatabaseProfile:
    return DatabaseProfile(
        id="fineract",
        name="Fineract",
        description="Core banking.",
        dialect="postgres",
        learned_at="2026-07-21T00:00:00+00:00",
        tables=[
            TableProfile(name="m_loan", columns=[ColumnProfile(name="id", type="BIGINT")]),
            TableProfile(name="m_office", columns=[ColumnProfile(name="id", type="BIGINT")]),
        ],
    )


def test_allowed_tables_filters_schema_context() -> None:
    context = catalog_schema_context(_profile(), allowed_tables={"m_loan"})
    assert "m_loan" in context
    assert "m_office" not in context


def test_none_allowlist_includes_all_tables() -> None:
    context = catalog_schema_context(_profile())
    assert "m_loan" in context and "m_office" in context
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_schema_allowlist.py -v`
Expected: FAIL — `TypeError: catalog_schema_context() got an unexpected keyword argument 'allowed_tables'`.

- [ ] **Step 3: Add allow-list filtering**

In `catalog.py`, change the signature and add a filter at the top of `catalog_schema_context`:

```python
def catalog_schema_context(profile: DatabaseProfile, allowed_tables: set[str] | None = None) -> str:
    allowed = {name.lower() for name in allowed_tables} if allowed_tables is not None else None
    tables = [t for t in profile.tables if allowed is None or t.name.lower() in allowed]
```

Then change the render loop to iterate `tables` instead of `profile.tables` (i.e. `for table in tables:`).

In `database.py`, thread the allow-list through `schema_context` and `_schema_for_database`:

```python
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
```

Update `_schema_for_database` to accept and apply the allow-list:

```python
    def _schema_for_database(self, database_id: str, allowed_tables: set[str] | None = None) -> str:
        db_config = self.get_config(database_id)
        engine = self.get_engine(database_id)
        inspector = inspect(engine)

        blocked_tables = {table.lower() for table in db_config.blocked_tables}
        allowed = {name.lower() for name in allowed_tables} if allowed_tables is not None else None
        table_names = [
            table
            for table in (db_config.include_tables or inspector.get_table_names())
            if table.lower() not in blocked_tables
            and (allowed is None or table.lower() in allowed)
        ]
```

(The rest of `_schema_for_database` is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_schema_allowlist.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite regression check**

Run: `.venv/bin/pytest -q`
Expected: all pass (default `tables_by_db=None` and `allowed_tables=None` preserve behavior).

- [ ] **Step 6: Commit**

```bash
git add src/db_agentic_system/catalog.py src/db_agentic_system/database.py tests/test_schema_allowlist.py
git commit -m "feat(schema): optional per-database table allow-list"
```

---

### Task 6: `select_tables` graph node

**Files:**
- Modify: `src/db_agentic_system/state.py` (add `selected_tables`)
- Modify: `src/db_agentic_system/graph.py` (new node + wiring + schema node uses allow-list)
- Test: `tests/test_select_tables_node.py` (new)

**Interfaces:**
- Consumes: `TableSelector.select` (Task 4); `schema_context(..., tables_by_db=...)` (Task 5); `AgentState`.
- Produces: `AgentState["selected_tables"]: dict[str, list[str]]`. Node runs between `route_databases` and `load_schema`; empty dict when no catalog.

- [ ] **Step 1: Add the state field**

In `src/db_agentic_system/state.py`, add to `AgentState`:

```python
    selected_databases: list[SelectedDatabase]
    selected_tables: dict[str, list[str]]
    schema_context: dict[str, str]
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_select_tables_node.py
import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import SimpleChatModel
from pydantic import Field

from db_agentic_system.catalog import build_catalog
from db_agentic_system.config import AgentConfig, DatabaseConfig
from db_agentic_system.graph import build_graph


class FakeChatModel(SimpleChatModel):
    responses: list[str] = Field(default_factory=list)
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake-chat-model"

    def _call(self, messages: list[Any], **kwargs: Any) -> str:
        response = self.responses[self.calls]
        self.calls += 1
        return response


class FakeEmbedder:
    def embed_query(self, text: str) -> list[float]:
        return [1.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] if "loan" in text else [0.0] for text in texts]


def test_select_tables_limits_tables_fed_to_sql(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DB_AGENT_PROVIDER", "test")
    monkeypatch.setattr("db_agentic_system.graph.build_embedder", lambda: FakeEmbedder())

    db_path = tmp_path / "test.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE m_loan (id INTEGER PRIMARY KEY, principal REAL);
        CREATE TABLE m_office (id INTEGER PRIMARY KEY, name TEXT);
        INSERT INTO m_loan VALUES (1, 500.0);
        INSERT INTO m_office VALUES (1, 'HQ');
        """
    )
    connection.close()

    config = AgentConfig(
        databases=[
            DatabaseConfig(
                id="core", name="Core", description="Loans and offices.",
                uri=f"sqlite:///{db_path}", dialect="sqlite",
            )
        ],
        max_selected_tables=1,
        max_sql_iterations=1,
    )
    catalog = build_catalog(config)

    llm = FakeChatModel(
        responses=[
            '{"plans":[{"database_id":"core","purpose":"loans","sql":"SELECT principal FROM m_loan"}]}',
            "There is one loan of 500.",
        ]
    )
    app = build_graph(config, llm=llm, catalog=catalog, schema_source="learned")
    result = app.invoke({"question": "show me loans", "forced_database_ids": ["core"]})

    assert result["selected_tables"] == {"core": ["m_loan"]}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_select_tables_node.py -v`
Expected: FAIL — `KeyError: 'selected_tables'` (node not wired yet).

- [ ] **Step 4: Implement the node and wiring**

In `graph.py`, add the import at the top:

```python
from db_agentic_system.table_selection import TableSelector
```

Inside `build_graph`, after the `router = SemanticDatabaseRouter(...)` block, capture the embedder once and build a selector. Change the router construction to reuse a single embedder:

```python
    embedder = build_embedder()
    router = SemanticDatabaseRouter(
        databases=config.databases,
        max_selected=config.max_selected_databases,
        min_score=config.min_route_score,
        embedder=embedder,
        catalog_text_by_database_id=catalog_texts,
    )
    table_selector = TableSelector(max_tables=config.max_selected_tables, embedder=embedder)
```

Add the node function (near `schema_node`):

```python
    def select_tables_node(state: AgentState) -> AgentState:
        if catalog is None:
            return {"selected_tables": {}}
        profiles = catalog.by_id()
        selected: dict[str, list[str]] = {}
        for database in state["selected_databases"]:
            profile = profiles.get(database["id"])
            if profile is None:
                continue
            selected[database["id"]] = table_selector.select(state["question"], profile)
        return {"selected_tables": selected}
```

Change `schema_node` to pass the allow-list:

```python
    def schema_node(state: AgentState) -> AgentState:
        database_ids = [database["id"] for database in state["selected_databases"]]
        return {
            "schema_context": registry.schema_context(
                database_ids,
                catalog,
                schema_source,
                tables_by_db=state.get("selected_tables") or None,
            )
        }
```

Register the node and rewire the edges. Replace the existing `graph.add_node("load_schema", schema_node)` region and the route→load_schema edge:

```python
    graph.add_node("route_databases", route_node)
    graph.add_node("select_tables", select_tables_node)
    graph.add_node("load_schema", schema_node)
```

```python
    graph.add_conditional_edges(
        "route_databases",
        _route_branch,
        {"selected": "select_tables", "no_database": END},
    )
    graph.add_edge("select_tables", "load_schema")
    graph.add_edge("load_schema", "generate_sql")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_select_tables_node.py -v`
Expected: PASS.

- [ ] **Step 6: Full suite regression check**

Run: `.venv/bin/pytest -q`
Expected: all pass. Existing `test_graph.py` runs with `catalog=None` ⇒ `select_tables_node` returns `{}` and `schema_context` gets `tables_by_db=None` ⇒ unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/db_agentic_system/state.py src/db_agentic_system/graph.py tests/test_select_tables_node.py
git commit -m "feat(graph): select_tables node narrows schema context per question"
```

---

### Task 7: AI auto-annotation pass + `--annotate` flag

**Files:**
- Create: `src/db_agentic_system/annotate.py`
- Modify: `src/db_agentic_system/cli.py` (add `--annotate` to `index`)
- Test: `tests/test_annotate.py` (new)

**Interfaces:**
- Consumes: `DatabaseCatalog` (Task 2); a `BaseChatModel`.
- Produces: `annotate_catalog(catalog: DatabaseCatalog, llm: BaseChatModel) -> DatabaseCatalog` — fills **empty** `table.description` and `column.description` from LLM JSON `{"description": str, "columns": {name: str}}`; never overwrites existing text. Consumed by `cli.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_annotate.py
from typing import Any

from langchain_core.language_models.chat_models import SimpleChatModel
from pydantic import Field

from db_agentic_system.annotate import annotate_catalog
from db_agentic_system.catalog import ColumnProfile, DatabaseCatalog, DatabaseProfile, TableProfile


class FakeChatModel(SimpleChatModel):
    responses: list[str] = Field(default_factory=list)
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake-chat-model"

    def _call(self, messages: list[Any], **kwargs: Any) -> str:
        response = self.responses[self.calls]
        self.calls += 1
        return response


def _catalog() -> DatabaseCatalog:
    return DatabaseCatalog(
        databases=[
            DatabaseProfile(
                id="fineract", name="Fineract", description="Core banking.",
                dialect="postgres", learned_at="2026-07-21T00:00:00+00:00",
                tables=[
                    TableProfile(
                        name="m_loan",
                        columns=[
                            ColumnProfile(name="id", type="BIGINT"),
                            ColumnProfile(name="loan_status_id", type="SMALLINT"),
                        ],
                    )
                ],
            )
        ]
    )


def test_annotate_fills_empty_descriptions() -> None:
    llm = FakeChatModel(
        responses=[
            '{"description":"A loan account.","columns":{"loan_status_id":"Loan lifecycle status."}}'
        ]
    )
    result = annotate_catalog(_catalog(), llm)
    table = result.databases[0].tables[0]
    assert table.description == "A loan account."
    assert table.columns[1].description == "Loan lifecycle status."


def test_annotate_does_not_overwrite_existing_description() -> None:
    catalog = _catalog()
    catalog.databases[0].tables[0].description = "Existing."
    llm = FakeChatModel(responses=['{"description":"New.","columns":{}}'])
    result = annotate_catalog(catalog, llm)
    assert result.databases[0].tables[0].description == "Existing."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_annotate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db_agentic_system.annotate'`.

- [ ] **Step 3: Implement `annotate.py`**

```python
# src/db_agentic_system/annotate.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_annotate.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Wire `--annotate` into the CLI**

In `cli.py`, add to the `index` subparser:

```python
    index.add_argument("--output", default="config/database_catalog.json", help="Output catalog path.")
    index.add_argument(
        "--annotate",
        action="store_true",
        help="Run an LLM pass to fill table/column descriptions.",
    )
```

In the `if args.command == "index":` block, build, then optionally annotate:

```python
    if args.command == "index":
        config = load_config(args.config)
        catalog = build_catalog(config)
        if args.annotate:
            from db_agentic_system.annotate import annotate_catalog
            from db_agentic_system.llm import build_chat_model

            catalog = annotate_catalog(catalog, build_chat_model())
        save_catalog(catalog, args.output)
        print(f"Learned {len(catalog.databases)} database(s) into {args.output}")
        return
```

- [ ] **Step 6: Full suite regression check**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/db_agentic_system/annotate.py src/db_agentic_system/cli.py tests/test_annotate.py
git commit -m "feat(annotate): opt-in LLM auto-annotation via db-agent index --annotate"
```

---

### Task 8: Deterministic enum enrichment from Fineract tables

**Files:**
- Create: `src/db_agentic_system/enum_enrichment.py`
- Modify: `src/db_agentic_system/catalog.py` (`build_catalog` calls enrichment per database)
- Test: `tests/test_enum_enrichment.py` (new)

**Interfaces:**
- Consumes: `DatabaseProfile` (Task 2); a SQLAlchemy `Engine`.
- Produces: `enrich_enums(profile: DatabaseProfile, engine: Engine) -> DatabaseProfile` — when `r_enum_value` exists, fills `ColumnProfile.enum_values` for columns whose name matches an `enum_name`; no-op otherwise. Called inside `build_catalog`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_enum_enrichment.py
from sqlalchemy import create_engine, text

from db_agentic_system.catalog import ColumnProfile, DatabaseProfile, TableProfile
from db_agentic_system.enum_enrichment import enrich_enums


def _profile() -> DatabaseProfile:
    return DatabaseProfile(
        id="fineract", name="Fineract", description="Core banking.",
        dialect="sqlite", learned_at="2026-07-21T00:00:00+00:00",
        tables=[
            TableProfile(
                name="m_loan",
                columns=[ColumnProfile(name="loan_status_id", type="SMALLINT")],
            )
        ],
    )


def test_enrich_fills_enum_values_from_r_enum_value(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'e.db'}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE r_enum_value (enum_name TEXT, enum_id INTEGER, enum_message_property TEXT)"
        ))
        conn.execute(text(
            "INSERT INTO r_enum_value VALUES "
            "('loan_status_id', 100, 'Submitted'), ('loan_status_id', 300, 'Active')"
        ))
    result = enrich_enums(_profile(), engine)
    assert result.tables[0].columns[0].enum_values == {"100": "Submitted", "300": "Active"}


def test_enrich_is_noop_without_r_enum_value(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE something (x INTEGER)"))
    result = enrich_enums(_profile(), engine)
    assert result.tables[0].columns[0].enum_values is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_enum_enrichment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db_agentic_system.enum_enrichment'`.

- [ ] **Step 3: Implement enrichment**

```python
# src/db_agentic_system/enum_enrichment.py
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from db_agentic_system.catalog import DatabaseProfile


def enrich_enums(profile: DatabaseProfile, engine: Engine) -> DatabaseProfile:
    if "r_enum_value" not in inspect(engine).get_table_names():
        return profile

    legend: dict[str, dict[str, str]] = {}
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT enum_name, enum_id, enum_message_property FROM r_enum_value")
        )
        for enum_name, enum_id, message in rows:
            legend.setdefault(str(enum_name), {})[str(enum_id)] = message

    for table in profile.tables:
        for column in table.columns:
            values = legend.get(column.name)
            if values:
                column.enum_values = values
    return profile
```

In `catalog.py` `build_catalog`, apply enrichment per database:

```python
def build_catalog(config: AgentConfig) -> DatabaseCatalog:
    from db_agentic_system.enum_enrichment import enrich_enums

    profiles = []
    for db_config in config.databases:
        engine = create_engine(db_config.resolved_uri)
        profile = profile_database(db_config, engine)
        profiles.append(enrich_enums(profile, engine))
    return DatabaseCatalog(databases=profiles)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_enum_enrichment.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Full suite regression check**

Run: `.venv/bin/pytest -q`
Expected: all pass (SQLite examples have no `r_enum_value` ⇒ no-op).

- [ ] **Step 6: Commit**

```bash
git add src/db_agentic_system/enum_enrichment.py src/db_agentic_system/catalog.py tests/test_enum_enrichment.py
git commit -m "feat(enum): decode Fineract enums from r_enum_value at index time"
```

---

### Task 9: Apply annotation overlay at catalog load

**Files:**
- Modify: `src/db_agentic_system/graph.py` (`build_graph` applies overlay)
- Test: `tests/test_overlay_at_load.py` (new)

**Interfaces:**
- Consumes: `merge_annotations`, `load_annotations` (Task 3); `AgentConfig.annotations_path` (Task 1).
- Produces: when `config.annotations_path` is set and the file exists, the loaded catalog is overlaid before use in routing and schema context.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_overlay_at_load.py
import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import SimpleChatModel
from pydantic import Field

from db_agentic_system.catalog import build_catalog
from db_agentic_system.config import AgentConfig, DatabaseConfig
from db_agentic_system.graph import build_graph


class FakeChatModel(SimpleChatModel):
    responses: list[str] = Field(default_factory=list)
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake-chat-model"

    def _call(self, messages: list[Any], **kwargs: Any) -> str:
        response = self.responses[self.calls]
        self.calls += 1
        return response


def test_overlay_descriptions_reach_schema_context(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DB_AGENT_PROVIDER", "test")
    db_path = tmp_path / "t.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("CREATE TABLE m_loan (id INTEGER PRIMARY KEY); INSERT INTO m_loan VALUES (1);")
    conn.close()

    annotations = tmp_path / "ann.yaml"
    annotations.write_text("tables:\n  m_loan:\n    description: The loan account overlay.\n")

    config = AgentConfig(
        databases=[
            DatabaseConfig(id="core", name="Core", description="d",
                           uri=f"sqlite:///{db_path}", dialect="sqlite")
        ],
        annotations_path=str(annotations),
    )
    catalog = build_catalog(config)

    llm = FakeChatModel(responses=['{"plans":[]}', "no data"])

    app = build_graph(config, llm=llm, catalog=catalog, schema_source="learned")
    result = app.invoke({"question": "loans", "forced_database_ids": ["core"]})
    assert "The loan account overlay." in result["schema_context"]["core"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_overlay_at_load.py -v`
Expected: FAIL — assertion error (overlay not applied; description absent from schema context).

- [ ] **Step 3: Apply overlay in `build_graph`**

In `graph.py`, add imports:

```python
from pathlib import Path
from db_agentic_system.annotations import load_annotations, merge_annotations
```

At the start of `build_graph`, after the catalog is resolved (`if catalog is None and config.catalog_path: catalog = load_catalog(...)`), overlay it:

```python
    if catalog is not None and config.annotations_path and Path(config.annotations_path).exists():
        catalog = merge_annotations(catalog, load_annotations(config.annotations_path))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_overlay_at_load.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite regression check**

Run: `.venv/bin/pytest -q`
Expected: all pass. Phase A is complete.

- [ ] **Step 6: Commit**

```bash
git add src/db_agentic_system/graph.py tests/test_overlay_at_load.py
git commit -m "feat(graph): apply annotation overlay when loading catalog"
```

---

# Phase B — Fineract infrastructure & data (Docker, verify against live stack)

### Task 10: Dependencies and env

**Files:**
- Modify: `pyproject.toml` (add `psycopg[binary]`; add `[project.optional-dependencies].seed`)
- Modify: `.env.example` (add `DATABASE_URL_FINERACT`)

- [ ] **Step 1: Add the Postgres driver and seed extra**

In `pyproject.toml`, add to `dependencies` (after `sqlalchemy>=2.0.0`):

```toml
  "psycopg[binary]>=3.2.0",
```

Add a new optional-dependencies group (below the existing `dev` group):

```toml
[project.optional-dependencies]
dev = [
  "pytest>=8.0.0",
  "ruff>=0.6.0",
]
seed = [
  "requests>=2.32.0",
]
```

- [ ] **Step 2: Install and verify the driver imports**

Run:
```bash
.venv/bin/pip install -e ".[dev,seed]"
.venv/bin/python -c "import psycopg, requests; print('drivers ok')"
```
Expected: prints `drivers ok`.

- [ ] **Step 3: Add the env var**

In `.env.example`, under the remote-database section, add:

```
# Fineract example (read-only role created after the stack is up — see fineract/README.md):
DATABASE_URL_FINERACT=postgresql+psycopg://agent_ro:agent_ro_pw@localhost:5432/fineract_default
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml .env.example
git commit -m "chore: add psycopg driver, seed extra, and Fineract DB URL example"
```

---

### Task 11: Fineract Docker Compose stack

**Files:**
- Create: `fineract/docker-compose.yml`
- Create: `fineract/postgres-init/01-databases.sql`
- Create: `fineract/README.md`

**Interfaces:**
- Produces: a running Fineract at `https://localhost:8443` and Postgres at `localhost:5432` with databases `fineract_tenants` and `fineract_default`, superuser `postgres` / password `fineract_pg_pw`.

- [ ] **Step 1: Write the Postgres init script**

```sql
-- fineract/postgres-init/01-databases.sql
-- Runs once at first Postgres init (as POSTGRES_USER). Fineract migrates the schema on boot.
CREATE DATABASE fineract_tenants;
CREATE DATABASE fineract_default;
```

- [ ] **Step 2: Write the self-contained compose file**

```yaml
# fineract/docker-compose.yml
# FOR LOCAL TESTING ONLY. Self-contained (no external env files).
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: fineract_pg_pw
      POSTGRES_DB: postgres
    ports:
      - "5432:5432"
    volumes:
      - ./postgres-init:/docker-entrypoint-initdb.d:ro
      - fineract_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 20

  fineract:
    image: apache/fineract:latest
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8443:8443"
    environment:
      FINERACT_HIKARI_DRIVER_SOURCE_CLASS_NAME: org.postgresql.Driver
      FINERACT_HIKARI_JDBC_URL: jdbc:postgresql://db:5432/fineract_tenants
      FINERACT_HIKARI_USERNAME: postgres
      FINERACT_HIKARI_PASSWORD: fineract_pg_pw
      FINERACT_DEFAULT_TENANTDB_HOSTNAME: db
      FINERACT_DEFAULT_TENANTDB_PORT: "5432"
      FINERACT_DEFAULT_TENANTDB_UID: postgres
      FINERACT_DEFAULT_TENANTDB_PWD: fineract_pg_pw
      FINERACT_DEFAULT_TENANTDB_IDENTIFIER: default
      FINERACT_DEFAULT_TENANTDB_NAME: fineract_default
      FINERACT_DEFAULT_TENANTDB_TIMEZONE: Asia/Kolkata
      FINERACT_DEFAULT_TENANTDB_CONN_PARAMS: ""
      FINERACT_SERVER_SSL_ENABLED: "true"
      FINERACT_DEFAULT_MASTER_PASSWORD: fineract
      FINERACT_INSECURE_HTTP_CLIENT: "true"
      JAVA_TOOL_OPTIONS: "-Xmx1G"

volumes:
  fineract_pgdata:
```

- [ ] **Step 3: Bring the stack up**

Run:
```bash
docker compose -f fineract/docker-compose.yml up -d
```
Expected: `db` and `fineract` containers start.

- [ ] **Step 4: Verify the image tag resolved**

If `up -d` errors with `manifest unknown` for `apache/fineract:latest`, list valid tags and pin one:
```bash
docker pull apache/fineract:latest || \
  echo "Pick a tag from https://hub.docker.com/r/apache/fineract/tags and set it in docker-compose.yml"
```
Expected: image pulls, or you pin a concrete tag (e.g. a recent release) in `fineract/docker-compose.yml` and re-run Step 3.

- [ ] **Step 5: Wait for Fineract to finish booting and verify health**

Run (polls the API root until it answers; Fineract boot + Liquibase migration takes 1–3 min):
```bash
for i in $(seq 1 60); do
  code=$(curl -sk -o /dev/null -w "%{http_code}" https://localhost:8443/fineract-provider/actuator/health || true)
  echo "attempt $i: $code"
  [ "$code" = "200" ] && break
  sleep 5
done
curl -sk https://localhost:8443/fineract-provider/actuator/health
```
Expected: final output shows HTTP 200 and `{"status":"UP"}` (or similar). If it never comes up, check `docker compose -f fineract/docker-compose.yml logs fineract` for schema/migration errors.

- [ ] **Step 6: Verify the schema was created**

Run:
```bash
docker compose -f fineract/docker-compose.yml exec -T db \
  psql -U postgres -d fineract_default -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"
```
Expected: a count in the low hundreds (~250–350 tables) — proves Fineract migrated the full schema.

- [ ] **Step 7: Write the runbook**

```markdown
<!-- fineract/README.md -->
# Fineract example stack

Local, testing-only Apache Fineract + PostgreSQL for the multi-database agent.

## Bring it up
    docker compose -f fineract/docker-compose.yml up -d
Wait 1–3 min for boot, then check:
    curl -sk https://localhost:8443/fineract-provider/actuator/health

- API base: https://localhost:8443/fineract-provider/api/v1 (self-signed TLS)
- Admin: mifos / password ; tenant header: Fineract-Platform-TenantId: default
- Postgres: localhost:5432, superuser postgres / fineract_pg_pw, DB fineract_default

## Seed data
    pip install -e ".[seed]"
    python fineract/seed_fineract.py

## Read-only role for the agent (run once, after boot)
    docker compose -f fineract/docker-compose.yml exec -T db psql -U postgres -f - < fineract/create-readonly-role.sql

## Index the catalog (with AI annotations)
    db-agent index --config config/fineract.example.yaml --output config/fineract_catalog.json --annotate

## Tear down (removes data)
    docker compose -f fineract/docker-compose.yml down -v
```

- [ ] **Step 8: Commit**

```bash
git add fineract/docker-compose.yml fineract/postgres-init/01-databases.sql fineract/README.md
git commit -m "feat(fineract): self-contained Docker Compose stack (Fineract + Postgres)"
```

---

### Task 12: Seed realistic banking data via REST

**Files:**
- Create: `fineract/seed_fineract.py`

**Interfaces:**
- Consumes: the running Fineract API (Task 11). Uses `requests` (Task 10 `[seed]` extra).
- Produces: an office, a loan officer, several clients, one loan product, one savings product, and per client active loans + savings accounts with repayments/deposits — populating `m_loan`, `m_loan_transaction`, `m_savings_account`, `m_savings_account_transaction`.

- [ ] **Step 1: Write the seed script**

```python
# fineract/seed_fineract.py
"""Seed a local Fineract with realistic banking data via the REST API.

Run after the stack is healthy:  python fineract/seed_fineract.py
Idempotency: re-running creates additional clients/loans; wipe with
`docker compose -f fineract/docker-compose.yml down -v` to start clean.
"""
from __future__ import annotations

import sys
import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://localhost:8443/fineract-provider/api/v1"
AUTH = ("mifos", "password")
HEADERS = {"Fineract-Platform-TenantId": "default", "Content-Type": "application/json"}
DATE_FMT = "dd MMMM yyyy"
LOCALE = "en"
OPENING_DATE = "01 January 2020"
TXN_DATE = "01 March 2024"


def call(method: str, path: str, payload: dict | None = None, params: dict | None = None) -> dict:
    response = requests.request(
        method, f"{BASE}{path}", json=payload, params=params,
        auth=AUTH, headers=HEADERS, verify=False, timeout=60,
    )
    if response.status_code >= 300:
        print(f"ERROR {method} {path} -> {response.status_code}\n{response.text}", file=sys.stderr)
        response.raise_for_status()
    return response.json() if response.content else {}


def get_template_ids() -> dict:
    """Discover valid codes from Fineract templates (version-robust)."""
    loan_tmpl = call("GET", "/loanproducts/template", params={"tenantIdentifier": "default"})
    currency = "USD"
    strategy = "mifos-standard-strategy"
    strategies = loan_tmpl.get("transactionProcessingStrategyOptions", [])
    if strategies:
        strategy = strategies[0].get("code", strategy)
    return {"currency": currency, "strategy": strategy}


def main() -> None:
    ids = get_template_ids()
    currency, strategy = ids["currency"], ids["strategy"]

    office_id = call("POST", "/offices", {
        "name": "Downtown Branch", "parentId": 1, "openingDate": OPENING_DATE,
        "dateFormat": DATE_FMT, "locale": LOCALE,
    })["officeId"]

    staff_id = call("POST", "/staff", {
        "officeId": office_id, "firstname": "Nadia", "lastname": "Officer",
        "isLoanOfficer": True, "joiningDate": OPENING_DATE,
        "dateFormat": DATE_FMT, "locale": LOCALE,
    })["resourceId"]

    loan_product_id = call("POST", "/loanproducts", {
        "name": "Standard Personal Loan", "shortName": "SPL1", "currencyCode": currency,
        "digitsAfterDecimal": 2, "inMultiplesOf": 1, "principal": 5000,
        "numberOfRepayments": 12, "repaymentEvery": 1, "repaymentFrequencyType": 2,
        "interestRatePerPeriod": 2, "interestRateFrequencyType": 2, "amortizationType": 1,
        "interestType": 0, "interestCalculationPeriodType": 1,
        "transactionProcessingStrategyCode": strategy, "accountingRule": 1,
        "dateFormat": DATE_FMT, "locale": LOCALE,
    })["resourceId"]

    savings_product_id = call("POST", "/savingsproducts", {
        "name": "Regular Savings", "shortName": "RS1", "description": "Basic savings",
        "currencyCode": currency, "digitsAfterDecimal": 2, "inMultiplesOf": 1,
        "nominalAnnualInterestRate": 5, "interestCompoundingPeriodType": 1,
        "interestPostingPeriodType": 4, "interestCalculationType": 1,
        "interestCalculationDaysInYearType": 365, "accountingRule": 1,
        "locale": LOCALE,
    })["resourceId"]

    clients = [
        ("Petra", "Yton"), ("Marco", "Reyes"), ("Amina", "Khan"),
        ("Diego", "Silva"), ("Fatima", "Noor"),
    ]
    for first, last in clients:
        client_id = call("POST", "/clients", {
            "officeId": office_id, "staffId": staff_id, "firstname": first, "lastname": last,
            "active": True, "activationDate": OPENING_DATE,
            "dateFormat": DATE_FMT, "locale": LOCALE,
        })["clientId"]

        loan_id = call("POST", "/loans", {
            "clientId": client_id, "productId": loan_product_id, "loanType": "individual",
            "principal": 5000, "loanTermFrequency": 12, "loanTermFrequencyType": 2,
            "numberOfRepayments": 12, "repaymentEvery": 1, "repaymentFrequencyType": 2,
            "interestRatePerPeriod": 2, "amortizationType": 1, "interestType": 0,
            "interestCalculationPeriodType": 1, "transactionProcessingStrategyCode": strategy,
            "expectedDisbursementDate": OPENING_DATE, "submittedOnDate": OPENING_DATE,
            "loanOfficerId": staff_id, "dateFormat": DATE_FMT, "locale": LOCALE,
        })["loanId"]
        call("POST", f"/loans/{loan_id}", {"approvedOnDate": OPENING_DATE,
             "dateFormat": DATE_FMT, "locale": LOCALE}, params={"command": "approve"})
        call("POST", f"/loans/{loan_id}", {"actualDisbursementDate": OPENING_DATE,
             "transactionAmount": 5000, "dateFormat": DATE_FMT, "locale": LOCALE},
             params={"command": "disburse"})
        call("POST", f"/loans/{loan_id}/transactions", {"transactionDate": TXN_DATE,
             "transactionAmount": 450, "dateFormat": DATE_FMT, "locale": LOCALE},
             params={"command": "repayment"})

        savings_id = call("POST", "/savingsaccounts", {
            "clientId": client_id, "productId": savings_product_id,
            "submittedOnDate": OPENING_DATE, "dateFormat": DATE_FMT, "locale": LOCALE,
        })["savingsId"]
        call("POST", f"/savingsaccounts/{savings_id}", {"approvedOnDate": OPENING_DATE,
             "dateFormat": DATE_FMT, "locale": LOCALE}, params={"command": "approve"})
        call("POST", f"/savingsaccounts/{savings_id}", {"activationDate": OPENING_DATE,
             "dateFormat": DATE_FMT, "locale": LOCALE}, params={"command": "activate"})
        call("POST", f"/savingsaccounts/{savings_id}/transactions", {"transactionDate": TXN_DATE,
             "transactionAmount": 1200, "dateFormat": DATE_FMT, "locale": LOCALE},
             params={"command": "deposit"})

        print(f"seeded client {first} {last}: loan {loan_id}, savings {savings_id}")

    print("Seeding complete.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the seed script**

Run:
```bash
.venv/bin/python fineract/seed_fineract.py
```
Expected: five `seeded client ...` lines then `Seeding complete.`

**If a call returns 400/403:** the script prints the endpoint, status, and Fineract's JSON error to stderr. Fix the offending field (enum codes like `repaymentFrequencyType`/`interestType`/`accountingRule` and the `transactionProcessingStrategyCode` are the version-sensitive ones — cross-check against `GET /loanproducts/template` and `GET /savingsproducts/template`) and re-run. Wipe first if partially seeded: `docker compose -f fineract/docker-compose.yml down -v && docker compose -f fineract/docker-compose.yml up -d`, wait for health, then re-run.

- [ ] **Step 3: Verify data landed in the DB**

Run:
```bash
docker compose -f fineract/docker-compose.yml exec -T db psql -U postgres -d fineract_default -c \
  "SELECT (SELECT count(*) FROM m_client) clients, (SELECT count(*) FROM m_loan) loans, \
   (SELECT count(*) FROM m_loan_transaction) loan_txns, \
   (SELECT count(*) FROM m_savings_account_transaction) sav_txns;"
```
Expected: `clients >= 5`, `loans >= 5`, and non-zero transaction counts.

- [ ] **Step 4: Commit**

```bash
git add fineract/seed_fineract.py
git commit -m "feat(fineract): REST seed script for clients, loans, savings, transactions"
```

---

### Task 13: Read-only role, config, annotations, and catalog index

**Files:**
- Create: `fineract/create-readonly-role.sql`
- Create: `config/fineract.example.yaml`
- Create: `config/fineract_annotations.yaml`
- Create (generated): `config/fineract_catalog.json`

**Interfaces:**
- Consumes: seeded Fineract DB (Task 12); `db-agent index --annotate` (Task 7); enum enrichment (Task 8); overlay (Tasks 3/9).
- Produces: `config/fineract_catalog.json` with the full schema, enum legends, and descriptions.

- [ ] **Step 1: Write the read-only role script**

```sql
-- fineract/create-readonly-role.sql
-- Run once, AFTER Fineract has migrated the schema, against the postgres superuser.
DROP ROLE IF EXISTS agent_ro;
CREATE ROLE agent_ro LOGIN PASSWORD 'agent_ro_pw';
GRANT CONNECT ON DATABASE fineract_default TO agent_ro;
\connect fineract_default
GRANT USAGE ON SCHEMA public TO agent_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agent_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO agent_ro;
```

- [ ] **Step 2: Create the read-only role**

Run:
```bash
docker compose -f fineract/docker-compose.yml exec -T db psql -U postgres -f - < fineract/create-readonly-role.sql
```
Expected: `CREATE ROLE`, `GRANT`, `ALTER DEFAULT PRIVILEGES` with no errors.

- [ ] **Step 3: Verify the read-only role can SELECT but not write**

Run:
```bash
docker compose -f fineract/docker-compose.yml exec -T -e PGPASSWORD=agent_ro_pw db \
  psql -U agent_ro -d fineract_default -c "SELECT count(*) FROM m_client;" \
  -c "INSERT INTO m_client(id) VALUES (999999);"
```
Expected: the SELECT returns a count; the INSERT fails with `ERROR: permission denied for table m_client`.

- [ ] **Step 4: Write the Fineract config**

```yaml
# config/fineract.example.yaml
catalog_path: config/fineract_catalog.json
annotations_path: config/fineract_annotations.yaml
max_selected_databases: 1
max_selected_tables: 25
min_route_score: 0.0
max_rows: 100
max_sql_iterations: 5

databases:
  - id: fineract
    name: Fineract Core Banking
    description: >-
      Apache Fineract core banking system. Clients (m_client), their offices
      (m_office) and loan officers (m_staff). Loan accounts (m_loan) with
      products (m_product_loan), disbursements and repayments (m_loan_transaction).
      Savings accounts (m_savings_account) with products (m_savings_product) and
      deposits/withdrawals (m_savings_account_transaction). Payments, charges,
      currencies, and enum-coded statuses decoded via r_enum_value.
    uri_env: DATABASE_URL_FINERACT
    dialect: postgres
    include_tables: []
    blocked_tables: []
    blocked_columns: []
    auto_block_sensitive_columns: true
    sample_rows: 0
```

- [ ] **Step 5: Write the hand-authored annotation overlay (core tables)**

```yaml
# config/fineract_annotations.yaml
# Human-authored descriptions override AI-generated ones. Cover the core banking tables;
# edit freely and restart the UI (no re-index needed).
tables:
  m_office:
    description: Branch/office hierarchy. parent_id links to the parent office.
  m_staff:
    description: Employees; is_loan_officer marks loan officers assignable to clients/loans.
  m_client:
    description: A customer (individual). office_id is their branch; status_enum is lifecycle state.
    columns:
      status_enum: Client lifecycle status (see r_enum_value).
      activation_date: Date the client became active.
  m_product_loan:
    description: Loan product definitions (terms, interest, currency).
  m_loan:
    description: A loan account held by a client. loan_status_id is its lifecycle status.
    columns:
      loan_status_id: Loan lifecycle status (see r_enum_value).
      principal_amount: Approved principal.
      principal_disbursed_derived: Total principal disbursed.
      principal_outstanding_derived: Principal still owed.
      client_id: The borrowing client.
      product_id: The loan product used.
  m_loan_transaction:
    description: Ledger of loan events — disbursements, repayments, waivers.
    columns:
      transaction_type_enum: Type of loan transaction (see r_enum_value).
      amount: Transaction amount.
      transaction_date: When the transaction occurred.
  m_savings_product:
    description: Savings product definitions (interest, posting periods, currency).
  m_savings_account:
    description: A savings account held by a client. account_balance_derived is the current balance.
    columns:
      status_enum: Savings account lifecycle status (see r_enum_value).
      account_balance_derived: Current savings balance.
      client_id: The account holder.
  m_savings_account_transaction:
    description: Ledger of savings events — deposits, withdrawals, interest postings.
    columns:
      transaction_type_enum: Type of savings transaction (see r_enum_value).
      amount: Transaction amount.
  m_payment_detail:
    description: Payment metadata (payment type, receipt/cheque numbers) linked to transactions.
  m_currency:
    description: Currencies enabled on the platform.
  m_charge:
    description: Fees/charges configurable against loan and savings products.
```

- [ ] **Step 6: Point the env at the read-only role and index the catalog**

Ensure `.env` has `DATABASE_URL_FINERACT=postgresql+psycopg://agent_ro:agent_ro_pw@localhost:5432/fineract_default`, then run:
```bash
.venv/bin/db-agent index --config config/fineract.example.yaml --output config/fineract_catalog.json --annotate
```
Expected: `Learned 1 database(s) into config/fineract_catalog.json`. (The `--annotate` pass needs a working model provider in `.env`; without it, drop `--annotate` — the hand overlay still covers the core tables.)

- [ ] **Step 7: Verify the catalog has full schema, enums, and descriptions**

Run:
```bash
.venv/bin/python -c "
from db_agentic_system.catalog import load_catalog
c = load_catalog('config/fineract_catalog.json')
p = c.databases[0]
print('tables:', len(p.tables))
loan = next(t for t in p.tables if t.name == 'm_loan')
status = next(col for col in loan.columns if col.name == 'loan_status_id')
print('enum_values present:', bool(status.enum_values))
"
```
Expected: `tables:` in the low hundreds and `enum_values present: True`.

- [ ] **Step 8: Commit**

```bash
git add fineract/create-readonly-role.sql config/fineract.example.yaml config/fineract_annotations.yaml config/fineract_catalog.json
git commit -m "feat(fineract): read-only role, config, annotations, and indexed catalog"
```

---

### Task 14: Register the UI example and verify end-to-end

**Files:**
- Modify: `src/db_agentic_system/static/app.js` (add a `DATA_PROFILES` entry)

- [ ] **Step 1: Add the Fineract profile**

In `src/db_agentic_system/static/app.js`, add to the `DATA_PROFILES` array (after the existing entries, before the closing `]`):

```javascript
  {
    id: "fineract:learned",
    label: "Fineract Core Banking · Learned catalog",
    configPath: "config/fineract.example.yaml",
    catalogPath: "config/fineract_catalog.json",
    schemaSource: "learned",
    catalogReady: true,
  },
```

- [ ] **Step 2: Start the UI**

Run (uses the launch config on port 8123):
```bash
.venv/bin/db-agent-ui &
sleep 3
curl -s -X POST http://127.0.0.1:8123/api/databases \
  -H 'Content-Type: application/json' \
  -d '{"config_path":"config/fineract.example.yaml","catalog_path":"config/fineract_catalog.json"}'
```
Expected: JSON listing the `fineract` database with a `tables` count in the low hundreds.

- [ ] **Step 3: Run an end-to-end grounded query against Fineract**

Run:
```bash
curl -s -X POST http://127.0.0.1:8123/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"How many active loans are there and what is the total outstanding principal?","config_path":"config/fineract.example.yaml","catalog_path":"config/fineract_catalog.json","schema_source":"learned","selected_database_ids":["fineract"]}'
```
Expected: a JSON response whose `answer` cites a loan count and total, `selected_databases` includes `fineract`, and `sql_plans` contains a SELECT against `m_loan` (not the whole 300-table schema). If the model provider is unconfigured, this returns a model error — configure `.env` and retry.

- [ ] **Step 4: Confirm table-selection narrowed the schema (spot check)**

Read the response's `sql_plans[].sql` from Step 3 — it should reference only loan-related tables. This confirms `select_tables` fed a small table set rather than all ~300.

- [ ] **Step 5: Stop the UI**

Run: `kill %1` (or find and kill the `db-agent-ui` process).

- [ ] **Step 6: Commit**

```bash
git add src/db_agentic_system/static/app.js
git commit -m "feat(ui): add Fineract Core Banking example to the database selector"
```

---

### Task 15: Documentation and final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the new example**

Add a "Fineract example" section to `README.md` describing: what it is (full-schema real core-banking DB), the bring-up/seed/index runbook (point to `fineract/README.md`), the `max_selected_tables` table-selection behavior, and the annotation overlay (`config/fineract_annotations.yaml`) that users can edit + restart (no re-index). Mention the read-only `agent_ro` role and that the agent connects read-only.

- [ ] **Step 2: Full test suite + lint**

Run:
```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests
```
Expected: all tests pass; ruff reports no errors.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the Fineract full-schema example"
```

---

## Self-Review Notes (coverage map)

- Spec §1 Infra & seed → Tasks 11, 12. Read-only role → Task 13.
- Spec §2 Config (`include_tables: []`, dialect, sample_rows, `max_selected_tables`, `annotations_path`, catalog) → Tasks 1, 13.
- Spec §3 Table-selection (module, node, state, allow-list, backward-compat) → Tasks 4, 5, 6.
- Spec §4 Annotation layer (catalog fields, overlay/user-wins, AI `--annotate`, enum enrichment, load-time overlay) → Tasks 2, 3, 7, 8, 9, 13.
- Spec §5 Wiring & deps (`psycopg`, `[seed]`, `.env.example`, `app.js`, README) → Tasks 10, 14, 15.
- Spec §6 Tests → Tasks 1–9 each ship tests; Task 4 (table-selection), Task 3/9 (annotations), Task 8 (enum), Task 1/13 (config parse).
- Security: read-only role (Task 13 Step 3 verifies write is denied); `auto_block_sensitive_columns: true` (Task 13 config); read-only SQL guard unchanged.

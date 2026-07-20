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


def test_allowed_tables_matching_is_case_insensitive() -> None:
    context = catalog_schema_context(_profile(), allowed_tables={"M_LOAN"})
    assert "m_loan" in context
    assert "m_office" not in context


def test_registry_schema_context_runtime_filters_by_tables_by_db(tmp_path) -> None:
    import sqlite3

    from db_agentic_system.config import AgentConfig, DatabaseConfig
    from db_agentic_system.database import DatabaseRegistry

    db_path = tmp_path / "r.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("CREATE TABLE m_loan (id INTEGER); CREATE TABLE m_office (id INTEGER);")
    conn.close()

    config = AgentConfig(
        databases=[
            DatabaseConfig(
                id="core", name="Core", description="d",
                uri=f"sqlite:///{db_path}", dialect="sqlite",
            )
        ]
    )
    registry = DatabaseRegistry(config)
    context = registry.schema_context(["core"], tables_by_db={"core": ["m_loan"]})
    assert "m_loan" in context["core"]
    assert "m_office" not in context["core"]

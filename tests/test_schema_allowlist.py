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

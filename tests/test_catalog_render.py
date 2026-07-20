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

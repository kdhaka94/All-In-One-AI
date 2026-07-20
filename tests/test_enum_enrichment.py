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

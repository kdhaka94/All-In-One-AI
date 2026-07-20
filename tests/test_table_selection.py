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

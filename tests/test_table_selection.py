from db_agentic_system.catalog import (
    ColumnProfile,
    DatabaseProfile,
    ForeignKeyProfile,
    TableProfile,
)
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


def test_cache_is_embedder_aware() -> None:
    from db_agentic_system import table_selection

    table_selection._VECTOR_CACHE.clear()
    profile = _profile()  # fixed id + learned_at shared by both selectors
    loan_selector = TableSelector(max_tables=1, embedder=FakeEmbedder("loan"))
    office_selector = TableSelector(max_tables=1, embedder=FakeEmbedder("office"))
    assert "m_loan" in loan_selector.select("show me loans", profile)
    assert "m_office" in office_selector.select("show me offices", profile)


def test_lexical_ranks_by_words_inside_identifiers_and_plurals() -> None:
    # Domain words live inside underscore identifiers; the query uses plurals.
    # m_office is FIRST, so with the old whole-identifier tokenizer both score 0
    # and top-1 would wrongly return m_office. Splitting on "_" + singularizing
    # lets m_loan match "outstanding"/"principal"/"loan".
    profile = DatabaseProfile(
        id="x", name="X", description="Bank.", dialect="postgres",
        learned_at="2026-07-21T00:00:00+00:00",
        tables=[
            TableProfile(name="m_office", columns=[ColumnProfile(name="name", type="TEXT")]),
            TableProfile(
                name="m_loan",
                columns=[ColumnProfile(name="principal_outstanding_derived", type="DECIMAL")],
            ),
        ],
    )
    selector = TableSelector(max_tables=1)  # lexical (no embedder)
    assert selector.select("total outstanding principal for loans", profile) == ["m_loan"]

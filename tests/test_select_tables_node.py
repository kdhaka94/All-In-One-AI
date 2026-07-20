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
            (
                '{"plans":[{"database_id":"core","purpose":"loans",'
                '"sql":"SELECT principal FROM m_loan"}]}'
            ),
            "There is one loan of 500.",
        ]
    )
    app = build_graph(config, llm=llm, catalog=catalog, schema_source="learned")
    result = app.invoke({"question": "show me loans", "forced_database_ids": ["core"]})

    assert result["selected_tables"] == {"core": ["m_loan"]}

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

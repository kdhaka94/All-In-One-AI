from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import SimpleChatModel
from pydantic import Field

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


def _people_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "people.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT, city TEXT);
        INSERT INTO people VALUES (1, 'Ada', 'London'), (2, 'Bo', 'Paris');
        """
    )
    connection.close()
    return db_path


def _config(db_path: Path, *, max_sql_iterations: int = 1) -> AgentConfig:
    return AgentConfig(
        databases=[
            DatabaseConfig(
                id="people_db",
                name="People DB",
                description="Only database.",
                uri=f"sqlite:///{db_path}",
                dialect="sqlite",
            )
        ],
        max_sql_iterations=max_sql_iterations,
    )


# A SELECT that passes safety validation (read-only, allowed table, no blocked
# columns) but references a column that does not exist, so the database rejects
# it at execution time.
_BAD_SQL_PLAN = (
    '{"plans":[{"database_id":"people_db","purpose":"find londoners",'
    '"sql":"SELECT name FROM people WHERE hometown = '
    "'London'\"}]}"
)
_REPAIRED_SQL_PLAN = (
    '{"plans":[{"database_id":"people_db","purpose":"corrected londoners query",'
    '"sql":"SELECT name FROM people WHERE city = '
    "'London'\"}]}"
)


def test_execution_error_degrades_gracefully_instead_of_raising(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DB_AGENT_PROVIDER", "test")
    db_path = _people_db(tmp_path)

    # Only one iteration: no repair attempt, so the failure must be handled in place.
    llm = FakeChatModel(responses=[_BAD_SQL_PLAN])
    config = _config(db_path, max_sql_iterations=1)

    app = build_graph(config, llm=llm, schema_source="runtime")
    result = app.invoke({"question": "Who lives in London?"})

    assert result["query_results"] == []
    assert result["execution_errors"], "the database failure should be captured, not raised"
    assert "people_db" in result["execution_errors"][0]
    assert "failed to run" in result["answer"].lower()


def test_agent_self_repairs_a_failed_query_and_answers(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DB_AGENT_PROVIDER", "test")
    db_path = _people_db(tmp_path)

    llm = FakeChatModel(
        responses=[
            _BAD_SQL_PLAN,       # 1) first attempt fails at execution
            _REPAIRED_SQL_PLAN,  # 2) repair pass returns corrected SQL
            "Ada lives in London.",  # 3) grounded answer from the repaired result
        ]
    )
    config = _config(db_path, max_sql_iterations=2)

    app = build_graph(config, llm=llm, schema_source="runtime")
    result = app.invoke({"question": "Who lives in London?"})

    assert result["answer"] == "Ada lives in London."
    assert result["execution_errors"], "the first failure should still be recorded"
    assert result["query_results"][-1]["rows"] == [{"name": "Ada"}]
    assert "corrected londoners query" in [plan["purpose"] for plan in result["sql_plans"]]


def test_web_commit_surfaces_execution_errors_in_response_and_trace(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DB_AGENT_SESSIONS_DB", str(tmp_path / "s.db"))
    from db_agentic_system import web

    web._SESSION_STORE = None  # force the lazy store to pick up the temp path
    web.SESSIONS.clear()

    session: dict[str, Any] = {"messages": [], "artifacts": [], "traces": []}
    result = {
        "answer": "I could not answer that because the database query failed to run.",
        "question": "Who lives in London?",
        "selected_databases": [{"id": "people_db", "name": "People DB"}],
        "sql_plans": [
            {"database_id": "people_db", "purpose": "find londoners", "sql": "SELECT name FROM people"}
        ],
        "validation_errors": [],
        "execution_errors": ["people_db: no such column: hometown"],
        "query_results": [],
    }

    response = web._commit_chat_result(
        "sid", session, "Who lives in London?", result, config_meta={}
    )

    assert response["execution_errors"] == ["people_db: no such column: hometown"]
    reloaded = web._store().load_session("sid")
    assert reloaded["traces"][-1]["execution_errors"] == ["people_db: no such column: hometown"]

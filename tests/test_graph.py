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


def test_agent_runs_bounded_followup_sql_when_results_reveal_new_predicates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DB_AGENT_PROVIDER", "test")
    db_path = tmp_path / "test.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE interviews (person_id INTEGER, transcript TEXT);
        CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT, hair_color TEXT, car_make TEXT);

        INSERT INTO interviews VALUES
          (1, 'The sponsor has red hair and drives a Tesla.');
        INSERT INTO people VALUES
          (10, 'Miranda Priestly', 'red', 'Tesla'),
          (11, 'Other Person', 'black', 'Tesla');
        """
    )
    connection.close()

    llm = FakeChatModel(
        responses=[
            (
                '{"plans":[{"database_id":"test_db","purpose":"read the known person clue",'
                '"sql":"SELECT transcript FROM interviews WHERE person_id = 1"}]}'
            ),
            (
                '{"plans":[{"database_id":"test_db","purpose":"find the person matching the clue",'
                '"sql":"SELECT name FROM people WHERE hair_color = '
                "'red' AND car_make = 'Tesla'\"}]}"
            ),
            "The person matching the clue is Miranda Priestly.",
        ]
    )
    config = AgentConfig(
        databases=[
            DatabaseConfig(
                id="test_db",
                name="Test DB",
                description="Only database.",
                uri=f"sqlite:///{db_path}",
                dialect="sqlite",
            )
        ],
        max_sql_iterations=2,
    )

    app = build_graph(config, llm=llm, schema_source="runtime")
    result = app.invoke({"question": "Who was behind it?"})

    assert result["answer"] == "The person matching the clue is Miranda Priestly."
    assert [plan["purpose"] for plan in result["sql_plans"]] == [
        "read the known person clue",
        "find the person matching the clue",
    ]
    assert result["query_results"][1]["rows"] == [{"name": "Miranda Priestly"}]


def test_agent_can_recover_from_empty_first_query_with_memory_guided_followups(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DB_AGENT_PROVIDER", "test")
    db_path = tmp_path / "test.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE interviews (person_id INTEGER, transcript TEXT);
        CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT, hair_color TEXT, car_make TEXT);

        INSERT INTO interviews VALUES
          (1, 'The sponsor has red hair and drives a Tesla.');
        INSERT INTO people VALUES
          (1, 'Jeremy Bowers', NULL, NULL),
          (10, 'Miranda Priestly', 'red', 'Tesla'),
          (11, 'Other Person', 'black', 'Tesla');
        """
    )
    connection.close()

    llm = FakeChatModel(
        responses=[
            "Was there another person involved with Jeremy Bowers?",
            (
                '{"plans":[{"database_id":"test_db","purpose":"broad keyword search",'
                '"sql":"SELECT name FROM people WHERE name LIKE '
                "'%accomplice%'\"}]}"
            ),
            (
                '{"plans":[{"database_id":"test_db","purpose":"read known actor evidence",'
                '"sql":"SELECT p.name, i.transcript FROM people p JOIN interviews i '
                "ON p.id = i.person_id WHERE p.name = 'Jeremy Bowers'\"}]}"
            ),
            (
                '{"plans":[{"database_id":"test_db","purpose":"find the related actor",'
                '"sql":"SELECT name FROM people WHERE hair_color = '
                "'red' AND car_make = 'Tesla'\"}]}"
            ),
            "Miranda Priestly was also involved.",
        ]
    )
    config = AgentConfig(
        databases=[
            DatabaseConfig(
                id="test_db",
                name="Test DB",
                description="Only database.",
                uri=f"sqlite:///{db_path}",
                dialect="sqlite",
            )
        ],
        max_sql_iterations=3,
    )

    app = build_graph(config, llm=llm, schema_source="runtime")
    result = app.invoke(
        {
            "question": "Was there anyone else involved?",
            "memory_context": "Recent answer: The murderer is Jeremy Bowers.",
        }
    )

    assert result["answer"] == "Miranda Priestly was also involved."
    assert [plan["purpose"] for plan in result["sql_plans"]] == [
        "broad keyword search",
        "read known actor evidence",
        "find the related actor",
    ]
    assert result["query_results"][0]["rows"] == []
    assert result["query_results"][2]["rows"] == [{"name": "Miranda Priestly"}]


def test_agent_answers_context_reasoning_question_when_no_sql_is_needed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DB_AGENT_PROVIDER", "test")
    db_path = tmp_path / "test.db"
    sqlite3.connect(db_path).close()

    llm = FakeChatModel(
        responses=[
            "Estimate our confidence that Miranda Priestly was involved based on prior evidence.",
            '{"plans":[]}',
            (
                "This is an inferential estimate, not a stored database value. Based on the "
                "available clues, I would be about 90% confident."
            ),
        ]
    )
    config = AgentConfig(
        databases=[
            DatabaseConfig(
                id="test_db",
                name="Test DB",
                description="Only database.",
                uri=f"sqlite:///{db_path}",
                dialect="sqlite",
            )
        ]
    )

    app = build_graph(config, llm=llm, schema_source="runtime")
    result = app.invoke(
        {
            "question": "guess",
            "conversation_history": [
                {"role": "user", "content": "Who was involved?"},
                {"role": "assistant", "content": "Miranda Priestly matched the clues."},
                {"role": "user", "content": "how much percentage sure are we?"},
                {"role": "assistant", "content": "I cannot compute a stored certainty."},
            ],
            "memory_context": (
                "Jeremy Bowers said the woman had red hair, was 65 to 67 inches tall, drove a "
                "Tesla Model S, and attended the SQL Symphony Concert 3 times. Miranda Priestly "
                "matched those predicates."
            ),
        }
    )

    assert result["sql_plans"] == []
    assert result["query_results"] == []
    assert "inferential estimate" in result["answer"]
    assert "90%" in result["answer"]


def test_missing_catalog_file_raises_an_actionable_error(tmp_path: Path) -> None:
    """A config naming a catalog that was never learned should say how to learn it.

    The catalogs are gitignored, so a fresh checkout hits this on its first question;
    the raw FileNotFoundError ("[Errno 2] ...") gave no hint about `db-agent index`.
    """
    missing_catalog = tmp_path / "bank_catalog.json"
    config = AgentConfig(
        catalog_path=str(missing_catalog),
        databases=[
            DatabaseConfig(
                id="bank",
                name="Retail Bank",
                description="Accounts and balances.",
                uri=f"sqlite:///{tmp_path / 'bank.db'}",
                dialect="sqlite",
            )
        ],
    )

    try:
        build_graph(config, llm=FakeChatModel(responses=[]))
    except ValueError as error:
        message = str(error)
        assert str(missing_catalog) in message
        assert "db-agent index" in message
        assert "Index Catalog" in message
    else:  # pragma: no cover - the guard is the point of the test
        raise AssertionError("build_graph should refuse a catalog path that does not exist")

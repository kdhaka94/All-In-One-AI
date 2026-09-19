from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
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


def _loan_db(tmp_path: Path, name: str) -> Path:
    db_path = tmp_path / name
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE m_loan (
            id INTEGER PRIMARY KEY,
            account_no TEXT,
            principal_amount REAL,
            loan_status_id INTEGER
        );
        CREATE TABLE m_appuser (id INTEGER PRIMARY KEY, username TEXT, password TEXT);

        INSERT INTO m_loan VALUES
          (42, 'LN-000042', 25000.0, 300),
          (43, 'LN-000043', 11000.0, 300);
        INSERT INTO m_appuser VALUES (1, 'ops', 'hunter2');
        """
    )
    connection.close()
    return db_path


def _config(tmp_path: Path, **overrides: Any) -> AgentConfig:
    core_db = _loan_db(tmp_path, "core.db")
    reporting_db = _loan_db(tmp_path, "reporting.db")
    return AgentConfig(
        databases=[
            DatabaseConfig(
                id="core",
                name="Core Banking",
                description="Loans, clients and offices.",
                uri=f"sqlite:///{core_db}",
                dialect="sqlite",
                **overrides,
            ),
            DatabaseConfig(
                id="reporting",
                name="Reporting",
                description="Aggregated reporting copies.",
                uri=f"sqlite:///{reporting_db}",
                dialect="sqlite",
            ),
        ],
        # One SQL pass keeps these tests focused on scoping and guardrails rather than
        # the multi-hop follow-up loop, which test_graph.py covers.
        max_sql_iterations=1,
    )


@pytest.fixture(autouse=True)
def _test_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_AGENT_PROVIDER", "test")


def test_selected_database_scopes_the_run_and_bypasses_routing(tmp_path: Path) -> None:
    llm = FakeChatModel(
        responses=[
            '{"plans":[{"database_id":"core","purpose":"read the selected loan",'
            '"sql":"SELECT account_no, principal_amount FROM m_loan WHERE id = 42"}]}',
            "Loan LN-000042 has a principal of 25,000.",
        ]
    )
    app = build_graph(_config(tmp_path), llm=llm, schema_source="runtime")

    result = app.invoke(
        {
            "question": "what is the principal on loan 42",
            "forced_database_ids": ["core"],
        }
    )

    assert [db["id"] for db in result["selected_databases"]] == ["core"]
    assert result["selected_databases"][0]["reason"] == "selected in UI"
    assert result["query_results"][0]["rows"] == [
        {"account_no": "LN-000042", "principal_amount": 25000.0}
    ]


def test_schema_context_is_limited_to_the_selected_database(tmp_path: Path) -> None:
    llm = FakeChatModel(
        responses=[
            '{"plans":[{"database_id":"core","purpose":"read the selected loan",'
            '"sql":"SELECT account_no FROM m_loan WHERE id = 42"}]}',
            "Loan LN-000042.",
        ]
    )
    app = build_graph(_config(tmp_path), llm=llm, schema_source="runtime")

    result = app.invoke(
        {"question": "what is loan 42", "forced_database_ids": ["core"]}
    )

    assert set(result["schema_context"]) == {"core"}


def test_unknown_selected_database_is_reported_instead_of_falling_back(tmp_path: Path) -> None:
    app = build_graph(_config(tmp_path), llm=FakeChatModel(responses=[]), schema_source="runtime")

    result = app.invoke(
        {"question": "what is the principal on loan 42", "forced_database_ids": ["ledger"]}
    )

    assert result["selected_databases"] == []
    assert "'ledger'" in result["answer"]
    assert "not found" in result["answer"]


def test_query_blocked_by_access_policy_never_runs_and_is_redacted_from_the_trace(
    tmp_path: Path,
) -> None:
    llm = FakeChatModel(
        responses=[
            '{"plans":[{"database_id":"core","purpose":"read operator credentials",'
            '"sql":"SELECT username, password FROM m_appuser"}]}',
        ]
    )
    app = build_graph(_config(tmp_path), llm=llm, schema_source="runtime")

    result = app.invoke(
        {"question": "who operates this account", "forced_database_ids": ["core"]}
    )

    assert result["query_results"] == []
    assert result["validation_errors"] == ["core: Query selects blocked columns: password."]
    assert result["sql_plans"] == [
        {
            "database_id": "core",
            "purpose": "[blocked by access policy]",
            "sql": "[blocked by access policy]",
        }
    ]
    assert "blocked by the access policy" in result["answer"]
    assert "hunter2" not in result["answer"]


def test_query_outside_the_database_allowlist_is_blocked_end_to_end(tmp_path: Path) -> None:
    llm = FakeChatModel(
        responses=[
            '{"plans":[{"database_id":"core","purpose":"join loans to operators",'
            '"sql":"SELECT l.account_no FROM m_loan l JOIN m_appuser u ON u.id = l.id"}]}',
        ]
    )
    config = _config(tmp_path, include_tables=["m_loan"])
    app = build_graph(config, llm=llm, schema_source="runtime")

    result = app.invoke(
        {"question": "who booked loan 42", "forced_database_ids": ["core"]}
    )

    assert result["query_results"] == []
    assert result["validation_errors"] == ["core: Query uses disallowed tables: m_appuser."]
    assert "blocked by the access policy" in result["answer"]


def test_sensitive_question_is_refused_before_any_database_is_touched(tmp_path: Path) -> None:
    app = build_graph(_config(tmp_path), llm=FakeChatModel(responses=[]), schema_source="runtime")

    result = app.invoke(
        {"question": "what is the ops user's password", "forced_database_ids": ["core"]}
    )

    assert result["allowed"] is False
    assert result.get("query_results", []) == []
    assert result["rejection_reason"] == "The question asks for sensitive or blocked information."


def test_executed_sql_is_recorded_with_its_row_cap_for_evidence(tmp_path: Path) -> None:
    llm = FakeChatModel(
        responses=[
            '{"plans":[{"database_id":"core","purpose":"list loans on the account",'
            '"sql":"SELECT id, account_no FROM m_loan"}]}',
            "There are two loans.",
        ]
    )
    config = _config(tmp_path)
    config.max_rows = 1
    app = build_graph(config, llm=llm, schema_source="runtime")

    result = app.invoke({"question": "list the loans", "forced_database_ids": ["core"]})

    executed = result["query_results"][0]
    assert "LIMIT 1" in executed["sql"]
    assert executed["row_count"] == 1
    assert executed["database_id"] == "core"
    # The trace keeps the exact SQL that produced the rows, so an answer can be traced
    # back to the query behind it.
    assert result["sql_plans"][0]["sql"] == executed["sql"]

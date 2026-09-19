from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import SimpleChatModel
from pydantic import Field

from db_agentic_system import web
from db_agentic_system.web import OpsBriefRequest, create_app


class FakeChatModel(SimpleChatModel):
    responses: list[str] = Field(default_factory=list)
    calls: int = 0
    prompts: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake-chat-model"

    def _call(self, messages: list[Any], **kwargs: Any) -> str:
        self.prompts.append("\n".join(str(message.content) for message in messages))
        response = self.responses[self.calls]
        self.calls += 1
        return response


@pytest.fixture(autouse=True)
def _test_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_AGENT_PROVIDER", "test")


@pytest.fixture
def banking(tmp_path: Path) -> str:
    """A small loan book, and a config whose ops screen investigates one loan."""
    db_path = tmp_path / "bank.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE m_loan (
            id INTEGER PRIMARY KEY,
            account_no TEXT,
            external_id TEXT,
            client_id INTEGER,
            principal_amount REAL,
            loan_status_id INTEGER
        );
        CREATE TABLE m_loan_transaction (id INTEGER PRIMARY KEY, loan_id INTEGER, amount REAL);
        CREATE TABLE m_client (id INTEGER PRIMARY KEY, display_name TEXT);
        CREATE TABLE m_appuser (id INTEGER PRIMARY KEY, username TEXT, password TEXT);

        INSERT INTO m_loan VALUES
          (42, 'LN-000042', 'EXT-42', 7, 25000.0, 300),
          (43, 'LN-000043', 'EXT-43', 8, 11000.0, 600);
        INSERT INTO m_loan_transaction VALUES (1, 42, 500.0), (2, 42, 500.0), (3, 43, 90.0);
        INSERT INTO m_client VALUES (7, 'Asha Rao'), (8, 'Other Client');
        INSERT INTO m_appuser VALUES (1, 'ops', 'hunter2');
        """
    )
    connection.close()

    config_path = tmp_path / "ops.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "max_rows": 100,
                "max_sql_iterations": 1,
                "table_selection_embeddings": False,
                "databases": [
                    {
                        "id": "core",
                        "name": "Core Banking",
                        "description": "Loans and clients.",
                        "uri": f"sqlite:///{db_path}",
                        "dialect": "sqlite",
                    }
                ],
                "ops": {
                    "database_id": "core",
                    "table": "m_loan",
                    "key_column": "id",
                    "label_columns": ["account_no"],
                    "search_columns": ["account_no", "external_id"],
                    "detail_columns": ["principal_amount", "loan_status_id"],
                    "alias_columns": ["loan_id"],
                    "related_columns": {"client_id": "m_client.id"},
                    "default_question": "Summarise this loan account.",
                },
            }
        )
    )
    return str(config_path)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _brief(banking: str, llm: FakeChatModel, **overrides: Any) -> dict[str, Any]:
    request = OpsBriefRequest(
        record_id=overrides.pop("record_id", "42"),
        config_path=banking,
        catalog_path=None,
        schema_source="runtime",
        **overrides,
    )
    return web._run_ops_brief(request, llm=llm)


def test_ops_screen_is_served(client: TestClient) -> None:
    response = client.get("/ops")
    assert response.status_code == 200
    assert "Operations" in response.text


def test_record_picker_lists_loans_for_selection(client: TestClient, banking: str) -> None:
    response = client.post("/api/ops/records", json={"config_path": banking})
    assert response.status_code == 200
    payload = response.json()

    assert payload["table"] == "m_loan"
    assert payload["key_column"] == "id"
    assert payload["label_columns"] == ["account_no"]
    assert [record["account_no"] for record in payload["records"]] == ["LN-000043", "LN-000042"]


def test_record_picker_search_matches_the_configured_columns(
    client: TestClient, banking: str
) -> None:
    response = client.post("/api/ops/records", json={"config_path": banking, "search": "000042"})
    records = response.json()["records"]

    assert [record["id"] for record in records] == [42]


def test_record_picker_search_term_cannot_inject_sql(client: TestClient, banking: str) -> None:
    # The term is bound as a parameter, so a quote in it is just a character
    # that matches nothing, never SQL.
    response = client.post(
        "/api/ops/records",
        json={"config_path": banking, "search": "' OR '1'='1"},
    )

    assert response.status_code == 200
    assert response.json()["records"] == []


def test_picker_is_rejected_when_the_profile_has_no_ops_section(client: TestClient) -> None:
    response = client.post(
        "/api/ops/records", json={"config_path": "config/bank.example.yaml"}
    )

    assert response.status_code == 400
    assert "no ops section" in response.json()["detail"]


def test_unknown_record_is_reported(client: TestClient, banking: str) -> None:
    response = client.post(
        "/api/ops/brief",
        json={"record_id": "999", "config_path": banking, "catalog_path": None},
    )

    assert response.status_code == 400
    assert "No m_loan record" in response.json()["detail"]


def test_brief_investigates_the_selected_loan_and_cites_its_evidence(banking: str) -> None:
    llm = FakeChatModel(
        responses=[
            '{"plans":['
            '{"database_id":"core","purpose":"read the loan",'
            '"sql":"SELECT account_no, principal_amount FROM m_loan WHERE id = 42"},'
            '{"database_id":"core","purpose":"read its repayments",'
            '"sql":"SELECT amount FROM m_loan_transaction WHERE loan_id = 42"}'
            "]}",
            "Loan LN-000042 is open with a principal of 25,000 [e1] and two repayments of "
            "500 have been received [e2].",
        ]
    )

    payload = _brief(banking, llm)

    assert payload["record"]["label"] == "LN-000042"
    assert payload["question"] == "Summarise this loan account."
    assert payload["scope"]["bindings"] == {"loan_id": "42", "client_id": "7"}

    assert [item["id"] for item in payload["evidence"]] == ["e1", "e2"]
    assert payload["evidence"][1]["rows"] == [{"amount": 500.0}, {"amount": 500.0}]
    assert payload["brief"]["cited"] == ["e1", "e2"]

    # Each citation in the brief resolves to a query that actually ran.
    evidence_by_id = {item["id"]: item for item in payload["evidence"]}
    for evidence_id in payload["brief"]["cited"]:
        assert "WHERE" in evidence_by_id[evidence_id]["sql"]
    assert payload["validation_errors"] == []


def test_the_selected_loan_is_the_only_one_the_investigation_can_read(banking: str) -> None:
    llm = FakeChatModel(
        responses=[
            '{"plans":['
            '{"database_id":"core","purpose":"compare against the book",'
            '"sql":"SELECT account_no, principal_amount FROM m_loan"},'
            '{"database_id":"core","purpose":"read its repayments",'
            '"sql":"SELECT amount FROM m_loan_transaction WHERE loan_id = 42"}'
            "]}",
            "Two repayments of 500 have been received [e1].",
        ]
    )

    payload = _brief(banking, llm)

    # The portfolio-wide query never ran, so no other loan reached the brief.
    assert len(payload["evidence"]) == 1
    assert payload["evidence"][0]["sql"].startswith("SELECT amount FROM m_loan_transaction")
    assert payload["blocked_query_count"] == 1
    assert "not scoped to LN-000042" in payload["validation_errors"][0]
    assert "LN-000043" not in str(payload["evidence"])


def test_a_query_for_another_loan_is_blocked_too(banking: str) -> None:
    llm = FakeChatModel(
        responses=[
            '{"plans":[{"database_id":"core","purpose":"peek at the neighbour",'
            '"sql":"SELECT account_no FROM m_loan WHERE id = 43"}]}',
        ]
    )

    payload = _brief(banking, llm)

    assert payload["evidence"] == []
    assert payload["blocked_query_count"] == 1
    assert payload["brief"]["text"].startswith("No evidence was gathered")


def test_blocked_columns_stay_blocked_inside_a_scoped_investigation(banking: str) -> None:
    llm = FakeChatModel(
        responses=[
            '{"plans":[{"database_id":"core","purpose":"who booked it",'
            '"sql":"SELECT username, password FROM m_appuser WHERE loan_id = 42"}]}',
        ]
    )

    payload = _brief(banking, llm)

    assert payload["evidence"] == []
    assert "blocked columns: password" in payload["validation_errors"][0]
    assert "hunter2" not in str(payload)


def test_the_planner_is_told_which_record_it_is_pinned_to(banking: str) -> None:
    llm = FakeChatModel(
        responses=[
            '{"plans":[{"database_id":"core","purpose":"read the loan",'
            '"sql":"SELECT account_no FROM m_loan WHERE id = 42"}]}',
            "Loan LN-000042 is open [e1].",
        ]
    )

    _brief(banking, llm)

    planning_prompt = llm.prompts[0]
    assert "RECORD SCOPE (mandatory)" in planning_prompt
    assert "m_loan.id = 42" in planning_prompt


def test_the_operator_can_ask_their_own_question(banking: str) -> None:
    llm = FakeChatModel(
        responses=[
            '{"plans":[{"database_id":"core","purpose":"read repayments",'
            '"sql":"SELECT amount FROM m_loan_transaction WHERE loan_id = 42"}]}',
            "Two repayments of 500 [e1].",
        ]
    )

    payload = _brief(banking, llm, question="Has this loan been repaid on time?")

    assert payload["question"] == "Has this loan been repaid on time?"
    assert "Has this loan been repaid on time?" in llm.prompts[0]


def test_a_brief_citing_evidence_that_does_not_exist_loses_the_citation(banking: str) -> None:
    llm = FakeChatModel(
        responses=[
            '{"plans":[{"database_id":"core","purpose":"read the loan",'
            '"sql":"SELECT account_no FROM m_loan WHERE id = 42"}]}',
            "The loan is open [e1] and was written off last year [e7].",
        ]
    )

    payload = _brief(banking, llm)

    assert payload["brief"]["cited"] == ["e1"]
    assert "e7" not in payload["brief"]["text"]

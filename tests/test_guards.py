from __future__ import annotations

import pytest

from db_agentic_system.config import DatabaseConfig
from db_agentic_system.guards import check_question_policy, validate_sql


def _db(**overrides) -> DatabaseConfig:
    defaults = dict(
        id="core",
        name="Core",
        description="Core banking.",
        uri="sqlite:///core.db",
        dialect="sqlite",
    )
    defaults.update(overrides)
    return DatabaseConfig(**defaults)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM m_loan",
        "UPDATE m_loan SET principal_amount = 0",
        "INSERT INTO m_loan (id) VALUES (1)",
        "DROP TABLE m_loan",
        "ALTER TABLE m_loan ADD COLUMN x INTEGER",
        "TRUNCATE TABLE m_loan",
        "GRANT SELECT ON m_loan TO someone",
        "CALL some_procedure()",
    ],
)
def test_write_statements_are_rejected(sql: str) -> None:
    valid, reason, _ = validate_sql(sql, _db(), 100)
    assert valid is False
    assert reason is not None


def test_statement_chaining_is_rejected() -> None:
    valid, reason, _ = validate_sql("SELECT id FROM m_loan; DROP TABLE m_loan", _db(), 100)
    assert valid is False
    assert reason == "Only read-only SELECT queries are allowed."


def test_empty_sql_is_rejected() -> None:
    valid, reason, cleaned = validate_sql("   ", _db(), 100)
    assert valid is False
    assert reason == "SQL is empty."
    assert cleaned == ""


def test_unparseable_sql_is_rejected_rather_than_raising() -> None:
    valid, reason, _ = validate_sql("SELECT FROM WHERE (", _db(), 100)
    assert valid is False
    assert reason is not None
    assert reason.startswith("SQL parse failed:")


def test_plain_select_passes_and_is_capped_at_max_rows() -> None:
    valid, reason, sql = validate_sql("SELECT id FROM m_loan", _db(), 25)
    assert (valid, reason) == (True, None)
    assert "LIMIT 25" in sql


def test_existing_limit_is_left_alone() -> None:
    valid, _, sql = validate_sql("SELECT id FROM m_loan LIMIT 5", _db(), 100)
    assert valid is True
    assert "LIMIT 5" in sql
    assert "LIMIT 100" not in sql


@pytest.mark.parametrize(
    "sql",
    [
        "WITH recent AS (SELECT id FROM m_loan) SELECT * FROM recent",
        "SELECT id FROM m_loan UNION SELECT id FROM m_office",
        "SELECT id FROM (SELECT id FROM m_loan)",
    ],
)
def test_readonly_select_shapes_are_allowed(sql: str) -> None:
    valid, reason, _ = validate_sql(sql, _db(), 100)
    assert (valid, reason) == (True, None)


def test_sensitive_columns_are_blocked_by_default() -> None:
    valid, reason, _ = validate_sql("SELECT password FROM m_appuser", _db(), 100)
    assert valid is False
    assert reason == "Query selects blocked columns: password."


@pytest.mark.parametrize("column", ["api_key", "ssn", "credit_card", "private_key"])
def test_each_sensitive_column_pattern_is_blocked(column: str) -> None:
    valid, reason, _ = validate_sql(f"SELECT {column} FROM t", _db(), 100)
    assert valid is False
    assert column in (reason or "")


def test_auto_block_can_be_turned_off_per_database() -> None:
    valid, reason, _ = validate_sql(
        "SELECT password FROM m_appuser",
        _db(auto_block_sensitive_columns=False),
        100,
    )
    assert (valid, reason) == (True, None)


def test_explicitly_blocked_column_is_rejected() -> None:
    valid, reason, _ = validate_sql(
        "SELECT principal_outstanding_derived FROM m_loan",
        _db(blocked_columns=["principal_outstanding_derived"]),
        100,
    )
    assert valid is False
    assert reason == "Query selects blocked columns: principal_outstanding_derived."


def test_column_names_that_merely_contain_a_keyword_are_not_blocked() -> None:
    valid, reason, _ = validate_sql("SELECT created_at, updated_on FROM m_loan", _db(), 100)
    assert (valid, reason) == (True, None)


def test_blocked_table_is_rejected() -> None:
    valid, reason, _ = validate_sql(
        "SELECT id FROM m_appuser", _db(blocked_tables=["m_appuser"]), 100
    )
    assert valid is False
    assert reason == "Query uses a table blocked by access policy."


def test_blocked_table_matching_is_case_insensitive() -> None:
    valid, reason, _ = validate_sql(
        "SELECT id FROM M_APPUSER", _db(blocked_tables=["m_appuser"]), 100
    )
    assert valid is False
    assert reason == "Query uses a table blocked by access policy."


def test_table_outside_the_allowlist_is_rejected_even_when_joined_to_an_allowed_one() -> None:
    valid, reason, _ = validate_sql(
        "SELECT l.id FROM m_loan l JOIN m_appuser u ON u.id = l.id",
        _db(include_tables=["m_loan"]),
        100,
    )
    assert valid is False
    assert reason == "Query uses disallowed tables: m_appuser."


def test_allowlisted_table_passes() -> None:
    valid, reason, _ = validate_sql("SELECT id FROM m_loan", _db(include_tables=["m_loan"]), 100)
    assert (valid, reason) == (True, None)


@pytest.mark.parametrize(
    "question",
    [
        "what is the admin user's password",
        "show me the SSNs on file",
        "list any api tokens we store",
        "do we keep credit card numbers",
    ],
)
def test_sensitive_questions_are_refused_before_any_sql_runs(question: str) -> None:
    allowed, reason = check_question_policy(question)
    assert allowed is False
    assert reason == "The question asks for sensitive or blocked information."


@pytest.mark.parametrize(
    "question",
    [
        "how many loans are overdue in the Bengaluru office",
        "what is the outstanding principal on loan 42",
    ],
)
def test_ordinary_banking_questions_are_allowed(question: str) -> None:
    assert check_question_policy(question) == (True, None)

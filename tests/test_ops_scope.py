from __future__ import annotations

import pytest
import sqlglot

from db_agentic_system.config import DatabaseConfig, OpsConfig
from db_agentic_system.guards import validate_sql
from db_agentic_system.scope import build_record_scope, safe_identifier, scope_violation


def _ops(**overrides) -> OpsConfig:
    defaults = dict(
        database_id="fineract",
        table="m_loan",
        key_column="id",
        label_columns=["account_no"],
        alias_columns=["loan_id"],
        related_columns={"client_id": "m_client.id"},
        reference_tables=["r_enum_value"],
    )
    defaults.update(overrides)
    return OpsConfig(**defaults)


def _record(**overrides):
    record = {"id": 42, "account_no": "LN-000042", "client_id": 7}
    record.update(overrides)
    return record


def _scope(**overrides):
    return build_record_scope(_ops(), _record(**overrides))


def _db() -> DatabaseConfig:
    return DatabaseConfig(
        id="fineract",
        name="Fineract",
        description="Core banking.",
        uri="sqlite:///core.db",
        dialect="sqlite",
    )


def _violation(sql: str, scope=None) -> str | None:
    return scope_violation(sqlglot.parse_one(sql, read="sqlite"), scope or _scope())


def test_scope_binds_the_key_its_aliases_and_related_columns() -> None:
    scope = _scope()
    # A foreign key named after the record holds on any table.
    assert scope.bindings == {"loan_id": "42", "client_id": "7"}
    # A key column only identifies the record on its own table, because id = 42
    # means a different record on every other one.
    assert scope.table_bindings == {"m_loan": {"id": "42"}, "m_client": {"id": "7"}}
    assert scope.binding_columns() == ["client_id", "id", "loan_id"]
    assert scope.label == "LN-000042"
    assert scope.key_value == 42


def test_the_key_column_does_not_bind_a_query_against_another_table() -> None:
    # m_loan_transaction.id = 42 is transaction 42, not loan 42.
    assert _violation("SELECT amount FROM m_loan_transaction WHERE id = 42") is not None


def test_related_column_missing_from_the_record_does_not_become_a_binding() -> None:
    # A loan with no client must not leave client_id as a free pass to any row.
    scope = build_record_scope(_ops(), {"id": 42, "account_no": "LN-000042", "client_id": None})
    assert "client_id" not in scope.bindings


def test_record_without_the_key_column_is_rejected() -> None:
    with pytest.raises(ValueError, match="no 'id' value"):
        build_record_scope(_ops(), {"account_no": "LN-000042"})


def test_scope_description_names_the_record_and_its_binding_columns() -> None:
    described = _scope().describe()
    assert "LN-000042" in described
    assert "m_loan.id = 42" in described
    assert "loan_id = 42" in described


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT account_no FROM m_loan WHERE id = 42",
        "SELECT amount FROM m_loan_transaction WHERE loan_id = 42",
        "SELECT due_date FROM m_loan_repayment_schedule WHERE loan_id = 42 AND completed = 0",
        "SELECT l.account_no, t.amount FROM m_loan l JOIN m_loan_transaction t "
        "ON t.loan_id = l.id WHERE l.id = 42",
        "SELECT display_name FROM m_client WHERE id = 7",
        "SELECT SUM(amount) FROM m_loan_transaction WHERE loan_id = 42",
    ],
)
def test_queries_bound_to_the_record_are_in_scope(sql: str) -> None:
    assert _violation(sql) is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT account_no FROM m_loan",
        "SELECT account_no FROM m_loan WHERE id = 43",
        "SELECT amount FROM m_loan_transaction WHERE loan_id = 99",
        "SELECT account_no FROM m_loan WHERE principal_amount > 1000",
        "SELECT COUNT(*) FROM m_loan_transaction",
    ],
)
def test_queries_that_leave_the_record_are_out_of_scope(sql: str) -> None:
    reason = _violation(sql)
    assert reason is not None
    assert "LN-000042" in reason


def test_scope_filter_under_an_or_does_not_count() -> None:
    # `id = 42 OR 1 = 1` reads the whole table, so it is not a record filter.
    assert _violation("SELECT account_no FROM m_loan WHERE id = 42 OR 1 = 1") is not None


def test_negated_scope_filter_does_not_count() -> None:
    assert _violation("SELECT account_no FROM m_loan WHERE NOT id = 42") is not None


def test_binding_written_with_the_value_first_is_accepted() -> None:
    assert _violation("SELECT account_no FROM m_loan WHERE 42 = id") is None


def test_numeric_key_matches_regardless_of_how_it_is_written() -> None:
    assert _violation("SELECT account_no FROM m_loan WHERE id = 42.0") is None


def test_another_records_client_does_not_satisfy_the_scope() -> None:
    assert _violation("SELECT display_name FROM m_client WHERE id = 8") is not None


def test_reference_table_lookup_needs_no_record_binding() -> None:
    assert _violation("SELECT enum_message_property FROM r_enum_value WHERE enum_id = 300") is None


def test_reference_table_joined_to_a_data_table_still_needs_a_binding() -> None:
    sql = (
        "SELECT e.enum_message_property FROM m_loan l "
        "JOIN r_enum_value e ON e.enum_id = l.loan_status_id"
    )
    assert _violation(sql) is not None


def test_guardrails_reject_an_out_of_scope_query_before_it_runs() -> None:
    valid, reason, _ = validate_sql("SELECT account_no FROM m_loan", _db(), 100, _scope())
    assert valid is False
    assert "not scoped to LN-000042" in (reason or "")


def test_guardrails_pass_a_scoped_query_and_still_apply_the_row_cap() -> None:
    valid, reason, sql = validate_sql(
        "SELECT amount FROM m_loan_transaction WHERE loan_id = 42", _db(), 20, _scope()
    )
    assert (valid, reason) == (True, None)
    assert "LIMIT 20" in sql


def test_a_scoped_query_is_still_subject_to_the_other_guardrails() -> None:
    # Being in scope does not buy an exemption from the read-only or column rules.
    valid, reason, _ = validate_sql(
        "SELECT password FROM m_appuser WHERE loan_id = 42", _db(), 100, _scope()
    )
    assert valid is False
    assert reason == "Query selects blocked columns: password."


def test_unscoped_validation_is_unchanged_when_no_record_is_selected() -> None:
    valid, reason, _ = validate_sql("SELECT account_no FROM m_loan", _db(), 100)
    assert (valid, reason) == (True, None)


@pytest.mark.parametrize("name", ["m_loan; DROP TABLE x", "id)", "", "1_bad drop"])
def test_configured_identifiers_are_checked_before_reaching_sql(name: str) -> None:
    with pytest.raises(ValueError, match="unsafe SQL identifier"):
        safe_identifier(name)

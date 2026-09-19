from __future__ import annotations

from db_agentic_system.config import OpsConfig
from db_agentic_system.ops import (
    EVIDENCE_ROW_LIMIT,
    blocked_plans,
    brief_segments,
    build_evidence,
    cited_evidence_ids,
    record_summary,
)
from db_agentic_system.scope import build_record_scope


def _ops() -> OpsConfig:
    return OpsConfig(
        database_id="fineract",
        table="m_loan",
        key_column="id",
        label_columns=["account_no"],
        detail_columns=["principal_amount"],
        alias_columns=["loan_id"],
        related_columns={"client_id": "m_client.id"},
    )


def _results() -> list[dict]:
    return [
        {
            "database_id": "fineract",
            "sql": "SELECT account_no FROM m_loan WHERE id = 42 LIMIT 100",
            "rows": [{"account_no": "LN-000042"}],
            "row_count": 1,
        },
        {
            "database_id": "fineract",
            "sql": "SELECT amount FROM m_loan_transaction WHERE loan_id = 42 LIMIT 100",
            "rows": [{"amount": 500.0}, {"amount": 500.0}],
            "row_count": 2,
        },
    ]


def _plans() -> list[dict]:
    return [
        {
            "database_id": "fineract",
            "purpose": "read the loan",
            "sql": "SELECT account_no FROM m_loan WHERE id = 42 LIMIT 100",
        },
        {
            "database_id": "fineract",
            "purpose": "read its repayments",
            "sql": "SELECT amount FROM m_loan_transaction WHERE loan_id = 42 LIMIT 100",
        },
    ]


def test_each_executed_query_becomes_numbered_evidence() -> None:
    evidence = build_evidence(_results(), _plans())

    assert [item["id"] for item in evidence] == ["e1", "e2"]
    assert [item["purpose"] for item in evidence] == ["read the loan", "read its repayments"]
    assert evidence[1]["sql"] == (
        "SELECT amount FROM m_loan_transaction WHERE loan_id = 42 LIMIT 100"
    )
    assert evidence[1]["rows"] == [{"amount": 500.0}, {"amount": 500.0}]
    assert evidence[1]["row_count"] == 2
    assert evidence[0]["truncated"] is False


def test_evidence_rows_are_capped_and_the_full_count_is_kept() -> None:
    rows = [{"amount": index} for index in range(EVIDENCE_ROW_LIMIT + 5)]
    evidence = build_evidence(
        [{"database_id": "fineract", "sql": "SELECT amount FROM t", "rows": rows}], []
    )

    assert len(evidence[0]["rows"]) == EVIDENCE_ROW_LIMIT
    assert evidence[0]["row_count"] == EVIDENCE_ROW_LIMIT + 5
    assert evidence[0]["truncated"] is True


def test_a_blocked_query_is_not_evidence() -> None:
    plans = [
        {
            "database_id": "fineract",
            "purpose": "[blocked by access policy]",
            "sql": "[blocked by access policy]",
        },
        *_plans(),
    ]
    evidence = build_evidence(_results(), plans)

    assert [item["id"] for item in evidence] == ["e1", "e2"]
    assert all(item["sql"] != "[blocked by access policy]" for item in evidence)
    assert blocked_plans(plans) == 1


def test_citations_become_clickable_segments_in_reading_order() -> None:
    evidence = build_evidence(_results(), _plans())
    segments = brief_segments(
        "The loan is active [e1] and two repayments have landed [e2].", evidence
    )

    assert [segment["type"] for segment in segments] == [
        "text",
        "citation",
        "text",
        "citation",
        "text",
    ]
    assert segments[0]["text"] == "The loan is active "
    assert segments[1]["evidence_id"] == "e1"
    assert segments[3]["evidence_id"] == "e2"
    assert cited_evidence_ids(segments) == ["e1", "e2"]


def test_every_citation_points_at_evidence_that_exists() -> None:
    evidence = build_evidence(_results(), _plans())
    segments = brief_segments("Active [e1], repaid twice [e2].", evidence)

    known = {item["id"] for item in evidence}
    cited = {segment["evidence_id"] for segment in segments if segment["type"] == "citation"}
    assert cited <= known


def test_a_citation_with_no_matching_evidence_is_dropped() -> None:
    # A link that opens nothing is worse than no link, and [e9] with two queries
    # is exactly the claim an operator should not trust.
    evidence = build_evidence(_results(), _plans())
    segments = brief_segments("Active [e1] and written off [e9].", evidence)

    assert cited_evidence_ids(segments) == ["e1"]
    assert "e9" not in "".join(
        segment["text"] for segment in segments if segment["type"] == "text"
    )


def test_the_same_evidence_cited_twice_is_listed_once() -> None:
    evidence = build_evidence(_results(), _plans())
    segments = brief_segments("Active [e1]. Still active [e1].", evidence)

    assert cited_evidence_ids(segments) == ["e1"]
    assert sum(1 for segment in segments if segment["type"] == "citation") == 2


def test_brief_without_citations_is_a_single_text_segment() -> None:
    segments = brief_segments("There is not enough evidence about this record.", [])

    assert segments == [
        {"type": "text", "text": "There is not enough evidence about this record."}
    ]
    assert cited_evidence_ids(segments) == []


def test_no_evidence_means_no_citation_survives() -> None:
    segments = brief_segments("The loan is active [e1].", [])

    assert cited_evidence_ids(segments) == []
    assert segments[0]["text"].startswith("The loan is active")


def test_record_summary_shows_the_record_and_what_pins_the_run_to_it() -> None:
    ops = _ops()
    record = {"id": 42, "account_no": "LN-000042", "principal_amount": 25000.0, "client_id": 7}
    summary = record_summary(ops, record, build_record_scope(ops, record))

    assert summary["id"] == 42
    assert summary["label"] == "LN-000042"
    assert summary["table"] == "m_loan"
    assert summary["fields"] == [
        {"name": "account_no", "value": "LN-000042"},
        {"name": "principal_amount", "value": 25000.0},
    ]
    assert summary["scope_columns"] == ["client_id", "id", "loan_id"]

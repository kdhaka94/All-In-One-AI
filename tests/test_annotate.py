from typing import Any

from langchain_core.language_models.chat_models import SimpleChatModel
from pydantic import Field

from db_agentic_system.annotate import annotate_catalog
from db_agentic_system.catalog import ColumnProfile, DatabaseCatalog, DatabaseProfile, TableProfile


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


def _catalog() -> DatabaseCatalog:
    return DatabaseCatalog(
        databases=[
            DatabaseProfile(
                id="fineract", name="Fineract", description="Core banking.",
                dialect="postgres", learned_at="2026-07-21T00:00:00+00:00",
                tables=[
                    TableProfile(
                        name="m_loan",
                        columns=[
                            ColumnProfile(name="id", type="BIGINT"),
                            ColumnProfile(name="loan_status_id", type="SMALLINT"),
                        ],
                    )
                ],
            )
        ]
    )


def test_annotate_fills_empty_descriptions() -> None:
    llm = FakeChatModel(
        responses=[
            '{"description":"A loan account.",'
            '"columns":{"loan_status_id":"Loan lifecycle status."}}'
        ]
    )
    result = annotate_catalog(_catalog(), llm)
    table = result.databases[0].tables[0]
    assert table.description == "A loan account."
    assert table.columns[1].description == "Loan lifecycle status."


def test_annotate_survives_columns_as_list() -> None:
    llm = FakeChatModel(responses=['{"description":"A loan.","columns":["id"]}'])
    result = annotate_catalog(_catalog(), llm)
    table = result.databases[0].tables[0]
    assert table.description == "A loan."
    assert all(c.description is None for c in table.columns)  # malformed columns ignored


def test_annotate_does_not_overwrite_existing_description() -> None:
    catalog = _catalog()
    catalog.databases[0].tables[0].description = "Existing."
    llm = FakeChatModel(responses=['{"description":"New.","columns":{}}'])
    result = annotate_catalog(catalog, llm)
    assert result.databases[0].tables[0].description == "Existing."

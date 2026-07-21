from typing import Any

from langchain_core.language_models.chat_models import SimpleChatModel
from pydantic import Field

from db_agentic_system.llm import synthesize_answer


class CaptureModel(SimpleChatModel):
    """Captures the messages sent to the model so we can assert on the prompt."""

    captured: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "capture"

    def _call(self, messages: list[Any], **kwargs: Any) -> str:
        self.captured.extend(messages)
        return "ok"


def test_synthesize_answer_prompt_requires_human_readable_no_raw_columns() -> None:
    model = CaptureModel()
    synthesize_answer(
        model,
        question="What loan products exist and the interest rate per period?",
        original_question="What loan products exist and the interest rate per period?",
        results=[{"database_id": "fineract", "sql": "SELECT ...", "rows": [], "row_count": 0}],
        validation_errors=[],
    )
    system_text = model.captured[0].content.lower()
    # Must instruct human-readable output and forbid echoing raw column names / enum codes.
    assert "human-readable" in system_text or "plain" in system_text
    assert "column name" in system_text
    assert "enum" in system_text

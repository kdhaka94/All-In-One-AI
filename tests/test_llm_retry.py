from types import SimpleNamespace

import pytest

from db_agentic_system import llm as llm_module


class _Recorder:
    """Minimal duck-typed chat model: raises on the first `fail_times` calls."""

    def __init__(self, fail_times: int, error: Exception, response: str = "ok") -> None:
        self.fail_times = fail_times
        self.error = error
        self.response = response
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error
        return SimpleNamespace(content=self.response)


def test_invoke_recovers_after_transient_rate_limit(monkeypatch) -> None:
    monkeypatch.setattr(llm_module, "_sleep", lambda seconds: None)  # no real waiting
    model = _Recorder(fail_times=2, error=RuntimeError("429 RESOURCE_EXHAUSTED"))
    result = llm_module._invoke(model, [])
    assert result.content == "ok"
    assert model.calls == 3  # failed twice, succeeded on the third attempt


def test_invoke_raises_clear_error_after_exhausting_retries(monkeypatch) -> None:
    monkeypatch.setattr(llm_module, "_sleep", lambda seconds: None)
    model = _Recorder(fail_times=99, error=RuntimeError("429 RESOURCE_EXHAUSTED quota"))
    with pytest.raises(Exception) as exc_info:
        llm_module._invoke(model, [], max_attempts=3)
    assert model.calls == 3
    assert "rate" in str(exc_info.value).lower()


def test_invoke_does_not_retry_non_retryable_error(monkeypatch) -> None:
    monkeypatch.setattr(llm_module, "_sleep", lambda seconds: None)
    model = _Recorder(fail_times=99, error=ValueError("400 invalid argument"))
    with pytest.raises(ValueError):
        llm_module._invoke(model, [])
    assert model.calls == 1  # non-retryable errors are not retried

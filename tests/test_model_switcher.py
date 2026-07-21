import pytest

from db_agentic_system.llm import (
    available_model_options,
    build_chat_model,
    resolve_model_option,
)


@pytest.fixture(autouse=True)
def _clear_keys(monkeypatch):
    for key in ["GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "OPENAI_ADMIN_KEY"]:
        monkeypatch.delenv(key, raising=False)


def test_available_model_options_filters_by_configured_provider_keys(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    ids = [o["id"] for o in available_model_options()]
    assert ids == ["gemini:gemini-3.1-flash-lite"]

    monkeypatch.setenv("OPENAI_API_KEY", "o")
    ids = {o["id"] for o in available_model_options()}
    assert ids == {"gemini:gemini-3.1-flash-lite", "openai:gpt-5.4-mini"}


def test_available_model_options_empty_when_no_keys() -> None:
    assert available_model_options() == []


def test_resolve_model_option() -> None:
    opt = resolve_model_option("openai:gpt-5.4-mini")
    assert opt["provider"] == "openai" and opt["model"] == "gpt-5.4-mini"
    assert resolve_model_option("nope") is None
    assert resolve_model_option(None) is None


def test_build_chat_model_honors_openai_override(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)
    build_chat_model(provider="openai", model="gpt-5.4-mini")
    assert captured["model"] == "gpt-5.4-mini"


def test_build_chat_model_override_beats_env_provider(monkeypatch) -> None:
    # Env says gemini, but the override forces openai — override must win.
    monkeypatch.setenv("DB_AGENT_PROVIDER", "gemini")
    monkeypatch.setenv("DB_AGENT_MODEL", "gemini-3.1-flash-lite")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)
    build_chat_model(provider="openai", model="gpt-5.4-mini")
    assert captured["model"] == "gpt-5.4-mini"


def test_build_chat_model_falls_back_to_env_when_no_override(monkeypatch) -> None:
    monkeypatch.setenv("DB_AGENT_PROVIDER", "openai")
    monkeypatch.setenv("DB_AGENT_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)
    build_chat_model()
    assert captured["model"] == "gpt-4.1-mini"


def test_build_embedder_honors_provider_override(monkeypatch) -> None:
    from db_agentic_system import router as router_module

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    captured = {}

    class FakeOpenAIEmbeddings:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("langchain_openai.OpenAIEmbeddings", FakeOpenAIEmbeddings)
    router_module.build_embedder(provider="openai", embedding_model="text-embedding-3-small")
    assert captured["model"] == "text-embedding-3-small"


def test_build_graph_threads_provider_and_model_to_chat_model(tmp_path, monkeypatch) -> None:
    import sqlite3
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
            return "fake"

        def _call(self, messages: list[Any], **kwargs: Any) -> str:
            response = self.responses[self.calls]
            self.calls += 1
            return response

    db_path = tmp_path / "t.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("CREATE TABLE t (id INTEGER); INSERT INTO t VALUES (1);")
    conn.close()

    captured = {}

    def fake_build_chat_model(provider=None, model=None):
        captured["provider"] = provider
        captured["model"] = model
        return FakeChatModel(responses=['{"plans":[]}', "no data"])

    monkeypatch.setattr("db_agentic_system.graph.build_chat_model", fake_build_chat_model)
    monkeypatch.setattr(
        "db_agentic_system.graph.build_embedder",
        lambda provider=None, embedding_model=None: None,
    )

    config = AgentConfig(
        databases=[
            DatabaseConfig(id="t", name="T", description="d",
                           uri=f"sqlite:///{db_path}", dialect="sqlite")
        ]
    )
    app = build_graph(config, provider="openai", model="gpt-5.4-mini", schema_source="runtime")
    app.invoke({"question": "hi", "forced_database_ids": ["t"]})
    assert captured == {"provider": "openai", "model": "gpt-5.4-mini"}


def test_model_overrides_resolves_known_id_and_ignores_unknown() -> None:
    from db_agentic_system.web import _model_overrides

    assert _model_overrides("openai:gpt-5.4-mini") == {
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "embedding_model": "text-embedding-3-small",
    }
    assert _model_overrides("nope") == {}
    assert _model_overrides(None) == {}

from db_agentic_system.sessions import SessionStore


def _session(*messages):
    msgs = []
    for role, content in messages:
        msgs.append({"role": role, "content": content})
    return {"messages": msgs, "artifacts": [{"note": "a"}]}


def test_traces_round_trip_and_default_to_empty(tmp_path) -> None:
    store = SessionStore(str(tmp_path / "s.db"))
    # Legacy session saved without a traces key loads as an empty list.
    store.save_session("legacy", _session(("user", "hi")))
    assert store.load_session("legacy")["traces"] == []

    session = _session(("user", "hi"), ("assistant", "hello"))
    session["traces"] = [{"standalone_question": "hi", "sql_plans": [{"sql": "SELECT 1"}]}]
    store.save_session("s1", session)
    loaded = store.load_session("s1")
    assert loaded["traces"] == session["traces"]


def test_save_and_load_round_trip(tmp_path) -> None:
    store = SessionStore(str(tmp_path / "s.db"))
    session = _session(("user", "hi"), ("assistant", "hello"))
    store.save_session(
        "s1", session, title="Greeting", config={"config_path": "c.yaml", "model_id": "openai:gpt-5.4-mini"}
    )
    loaded = store.load_session("s1")
    assert loaded["title"] == "Greeting"
    assert loaded["messages"] == session["messages"]
    assert loaded["artifacts"] == session["artifacts"]
    assert loaded["config"]["config_path"] == "c.yaml"
    assert loaded["config"]["model_id"] == "openai:gpt-5.4-mini"


def test_load_missing_returns_none(tmp_path) -> None:
    assert SessionStore(str(tmp_path / "s.db")).load_session("nope") is None


def test_list_sessions_orders_newest_first_with_message_count(tmp_path) -> None:
    store = SessionStore(str(tmp_path / "s.db"))
    store.save_session("a", _session(("user", "1")), title="A")
    store.save_session("b", _session(("user", "1"), ("assistant", "2")), title="B")
    listed = store.list_sessions()
    assert [s["id"] for s in listed] == ["b", "a"]  # b saved last -> newest first
    assert listed[0]["title"] == "B" and listed[0]["message_count"] == 2


def test_delete_session(tmp_path) -> None:
    store = SessionStore(str(tmp_path / "s.db"))
    store.save_session("a", _session(("user", "1")), title="A")
    store.delete_session("a")
    assert store.load_session("a") is None
    assert store.list_sessions() == []


def test_update_keeps_created_at_and_title_when_not_provided(tmp_path) -> None:
    store = SessionStore(str(tmp_path / "s.db"))
    store.save_session("a", _session(("user", "first")), title="First title")
    first = store.load_session("a")
    store.save_session("a", _session(("user", "first"), ("assistant", "reply")))  # no title
    second = store.load_session("a")
    assert second["title"] == "First title"  # preserved
    assert second["created_at"] == first["created_at"]  # preserved
    assert len(second["messages"]) == 2  # payload updated


def test_web_get_session_hydrates_from_store_after_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DB_AGENT_SESSIONS_DB", str(tmp_path / "s.db"))
    from db_agentic_system import web

    web._SESSION_STORE = None  # force the lazy store to pick up the temp path
    web.SESSIONS.clear()
    web._store().save_session(
        "sid", {"messages": [{"role": "user", "content": "hi"}], "artifacts": []}, title="T"
    )
    web.SESSIONS.clear()  # simulate a server restart: RAM is empty
    session = web._get_session("sid")
    assert session["messages"] == [{"role": "user", "content": "hi"}]

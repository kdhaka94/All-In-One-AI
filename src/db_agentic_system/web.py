from __future__ import annotations

import os
import uuid
import json
from importlib import resources
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db_agentic_system.catalog import build_catalog, load_catalog, save_catalog
from db_agentic_system.config import load_config
from db_agentic_system.llm import (
    available_model_options,
    default_model_id,
    gemini_api_key,
    resolve_model_option,
)
from db_agentic_system.graph import run_agent
from db_agentic_system.memory import build_memory_context, make_memory_artifact


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    config_path: str = "config/bank.example.yaml"
    catalog_path: str | None = "config/bank_catalog.json"
    schema_source: str = Field(default="learned", pattern="^(learned|runtime)$")
    selected_database_ids: list[str] = Field(default_factory=list)
    model_id: str | None = None


def _model_overrides(model_id: str | None) -> dict[str, str]:
    """Resolve a UI model id to provider/model/embedding overrides for run_agent."""
    option = resolve_model_option(model_id)
    if not option:
        return {}
    return {
        "provider": option["provider"],
        "model": option["model"],
        "embedding_model": option["embedding_model"],
    }


class IndexRequest(BaseModel):
    config_path: str = "config/bank.example.yaml"
    output_path: str = "config/bank_catalog.json"


class DatabaseListRequest(BaseModel):
    config_path: str = "config/bank.example.yaml"
    catalog_path: str | None = "config/bank_catalog.json"


class ResetRequest(BaseModel):
    session_id: str


SESSIONS: dict[str, dict[str, Any]] = {}


def create_app() -> FastAPI:
    app = FastAPI(title="Multi-Database Grounded Agent")
    static_dir = resources.files("db_agentic_system").joinpath("static")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(static_dir.joinpath("index.html")))

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        provider = os.getenv("DB_AGENT_PROVIDER", "gemini").lower()
        model_configured = (
            bool(gemini_api_key())
            if provider == "gemini"
            else bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_ADMIN_KEY"))
        )
        return {
            "provider": provider,
            "model": os.getenv(
                "DB_AGENT_MODEL",
                "gemini-3.1-flash-lite" if provider == "gemini" else "gpt-4.1-mini",
            ),
            "model_configured": model_configured,
            "default_config_exists": Path("config/bank.example.yaml").exists(),
            "default_catalog_exists": Path("config/bank_catalog.json").exists(),
            "session_count": len(SESSIONS),
        }

    @app.get("/api/models")
    def models() -> dict[str, Any]:
        return {"models": available_model_options(), "default_id": default_model_id()}

    @app.post("/api/index")
    def index_catalog(request: IndexRequest) -> dict[str, Any]:
        try:
            config = load_config(request.config_path)
            catalog = build_catalog(config)
            save_catalog(catalog, request.output_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "message": f"Learned {len(catalog.databases)} database(s).",
            "output_path": request.output_path,
            "databases": [
                {
                    "id": database.id,
                    "name": database.name,
                    "tables": len(database.tables),
                    "learned_at": database.learned_at,
                }
                for database in catalog.databases
            ],
        }

    @app.post("/api/databases")
    def databases(request: DatabaseListRequest) -> dict[str, Any]:
        try:
            config = load_config(request.config_path)
            catalog = _load_optional_catalog(request.catalog_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        catalog_by_id = catalog.by_id() if catalog else {}
        return {
            "databases": [
                {
                    "id": database.id,
                    "name": database.name,
                    "description": database.description,
                    "tables": (
                        len(catalog_by_id[database.id].tables)
                        if database.id in catalog_by_id
                        else None
                    ),
                }
                for database in config.databases
            ]
        }

    @app.post("/api/chat")
    def chat(request: ChatRequest) -> dict[str, Any]:
        try:
            return _run_chat(request)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/chat-stream")
    def chat_stream(request: ChatRequest) -> StreamingResponse:
        def generate():
            try:
                yield _json_line({"type": "step", "title": "Preparing session", "status": "running"})
                session_id = request.session_id or str(uuid.uuid4())
                session = _get_session(session_id)
                memory_context = build_memory_context(session["messages"], session["artifacts"])
                yield _json_line(
                    {
                        "type": "step",
                        "title": "Loaded memory",
                        "status": "complete",
                        "detail": f"{len(session['messages'])} visible turns, "
                        f"{len(session['artifacts'])} remembered traces",
                    }
                )

                yield _json_line({"type": "step", "title": "Loading catalog", "status": "running"})
                catalog = _load_optional_catalog(request.catalog_path)
                config = load_config(request.config_path)
                yield _json_line({"type": "step", "title": "Loading catalog", "status": "complete"})

                yield _json_line(
                    {
                        "type": "step",
                        "title": "Running agent",
                        "status": "running",
                        "detail": "Policy, follow-up rewrite, routing, SQL planning, validation, execution",
                    }
                )
                result = run_agent(
                    config,
                    request.message,
                    catalog=catalog,
                    schema_source=request.schema_source,
                    conversation_history=session["messages"],
                    memory_context=memory_context,
                    forced_database_ids=request.selected_database_ids,
                    **_model_overrides(request.model_id),
                )
                response = _commit_chat_result(session_id, session, request.message, result)
                yield _json_line({"type": "step", "title": "Running agent", "status": "complete"})
                yield _json_line({"type": "result", "data": response})
            except Exception as exc:
                yield _json_line({"type": "error", "message": str(exc)})

        return StreamingResponse(generate(), media_type="application/x-ndjson")

    @app.post("/api/reset")
    def reset(request: ResetRequest) -> dict[str, str]:
        SESSIONS.pop(request.session_id, None)
        return {"message": "Session reset."}

    return app


def _summarize_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summarized = []
    for result in results:
        rows = result.get("rows", [])
        summarized.append(
            {
                "database_id": result.get("database_id"),
                "sql": result.get("sql"),
                "row_count": result.get("row_count", len(rows)),
                "preview_rows": rows[:5],
            }
        )
    return summarized


def _run_chat(request: ChatRequest) -> dict[str, Any]:
    session_id = request.session_id or str(uuid.uuid4())
    session = _get_session(session_id)
    catalog = _load_optional_catalog(request.catalog_path)
    config = load_config(request.config_path)
    memory_context = build_memory_context(session["messages"], session["artifacts"])
    result = run_agent(
        config,
        request.message,
        catalog=catalog,
        schema_source=request.schema_source,
        conversation_history=session["messages"],
        memory_context=memory_context,
        forced_database_ids=request.selected_database_ids,
        **_model_overrides(request.model_id),
    )
    return _commit_chat_result(session_id, session, request.message, result)


def _get_session(session_id: str) -> dict[str, Any]:
    return SESSIONS.setdefault(session_id, {"messages": [], "artifacts": []})


def _load_optional_catalog(catalog_path: str | None):
    if not catalog_path:
        return None
    path = Path(catalog_path)
    if not path.exists():
        return None
    try:
        return load_catalog(path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid catalog: {exc}") from exc


def _commit_chat_result(
    session_id: str,
    session: dict[str, Any],
    user_message: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    answer = result.get("answer", "No answer produced.")
    summarized_results = _summarize_results(result.get("query_results", []))
    response = {
        "session_id": session_id,
        "answer": answer,
        "standalone_question": result.get("question", user_message),
        "selected_databases": result.get("selected_databases", []),
        "sql_plans": result.get("sql_plans", []),
        "validation_errors": result.get("validation_errors", []),
        "query_results": summarized_results,
        "history": session["messages"],
    }
    session["messages"].append({"role": "user", "content": user_message})
    session["messages"].append({"role": "assistant", "content": answer})
    session["artifacts"].append(make_memory_artifact(user_message, result, summarized_results))
    session["messages"] = session["messages"][-24:]
    session["artifacts"] = session["artifacts"][-12:]
    response["history"] = session["messages"]
    response["memory_artifacts"] = session["artifacts"][-3:]
    return response


def _json_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, default=str) + "\n"


def main() -> None:
    uvicorn.run(
        "db_agentic_system.web:create_app",
        factory=True,
        host=os.getenv("DB_AGENT_UI_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_AGENT_UI_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    main()

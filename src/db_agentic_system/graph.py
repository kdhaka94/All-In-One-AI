from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langchain_core.language_models.chat_models import BaseChatModel

from db_agentic_system.catalog import DatabaseCatalog, catalog_router_text, load_catalog
from db_agentic_system.config import AgentConfig
from db_agentic_system.database import DatabaseRegistry
from db_agentic_system.guards import check_question_policy, validate_sql
from db_agentic_system.llm import (
    build_chat_model,
    contextualize_question,
    generate_followup_sql_plans,
    generate_sql_plans,
    synthesize_answer,
    synthesize_context_answer,
)
from db_agentic_system.router import SemanticDatabaseRouter, build_embedder
from db_agentic_system.state import AgentState, QueryResult


def build_graph(
    config: AgentConfig,
    llm: BaseChatModel | None = None,
    catalog: DatabaseCatalog | None = None,
    schema_source: str = "runtime",
):
    if catalog is None and config.catalog_path:
        catalog = load_catalog(config.catalog_path)

    catalog_texts = None
    if catalog is not None:
        catalog_texts = {
            profile.id: catalog_router_text(profile)
            for profile in catalog.databases
        }

    registry = DatabaseRegistry(config)
    router = SemanticDatabaseRouter(
        databases=config.databases,
        max_selected=config.max_selected_databases,
        min_score=config.min_route_score,
        embedder=build_embedder(),
        catalog_text_by_database_id=catalog_texts,
    )
    llm_holder: dict[str, BaseChatModel | None] = {"llm": llm}

    def get_llm() -> BaseChatModel:
        if llm_holder["llm"] is None:
            llm_holder["llm"] = build_chat_model()
        return llm_holder["llm"]

    def policy_node(state: AgentState) -> AgentState:
        allowed, reason = check_question_policy(state["question"])
        update: AgentState = {"allowed": allowed, "rejection_reason": reason or ""}
        if not allowed:
            update["answer"] = reason or "This question is not allowed by the data access policy."
        return update

    def contextualize_node(state: AgentState) -> AgentState:
        original_question = state["question"]
        history = state.get("conversation_history", [])
        memory_context = state.get("memory_context", "")
        if not history and not memory_context:
            return {"original_question": original_question, "question": original_question}

        standalone_question = contextualize_question(
            get_llm(),
            original_question,
            history,
            memory_context,
        )
        allowed, reason = check_question_policy(standalone_question)
        if not allowed:
            return {
                "original_question": original_question,
                "question": standalone_question,
                "allowed": False,
                "rejection_reason": reason or "",
                "answer": reason or "This question is not allowed by the data access policy.",
            }
        return {"original_question": original_question, "question": standalone_question}

    def route_node(state: AgentState) -> AgentState:
        forced_database_ids = state.get("forced_database_ids", [])
        if forced_database_ids:
            selected = []
            for database_id in forced_database_ids:
                try:
                    db_config = registry.get_config(database_id)
                except KeyError:
                    return {
                        "selected_databases": [],
                        "answer": f"Configured database {database_id!r} was not found.",
                    }
                selected.append(
                    {
                        "id": db_config.id,
                        "name": db_config.name,
                        "reason": "selected in UI",
                        "score": 1.0,
                    }
                )
            return {"selected_databases": selected}

        selected = router.route(state["question"])
        if not selected:
            return {
                "selected_databases": [],
                "answer": "I could not identify a relevant database for this question.",
            }
        return {"selected_databases": selected}

    def schema_node(state: AgentState) -> AgentState:
        database_ids = [database["id"] for database in state["selected_databases"]]
        return {"schema_context": registry.schema_context(database_ids, catalog, schema_source)}

    def sql_node(state: AgentState) -> AgentState:
        plans = generate_sql_plans(
            get_llm(),
            state["question"],
            state["schema_context"],
            state.get("memory_context", ""),
        )
        return {"sql_plans": plans}

    def execute_node(state: AgentState) -> AgentState:
        query_results: list[QueryResult] = []
        validation_errors: list[str] = []
        sanitized_plans: list[dict[str, str]] = []
        pending_plans = list(state.get("sql_plans", []))
        seen_sql: set[tuple[str, str]] = set()

        for iteration in range(max(config.max_sql_iterations, 1)):
            executed_in_iteration = False
            for plan in pending_plans:
                database_id = plan["database_id"]
                db_config = registry.get_config(database_id)
                valid, reason, sql = validate_sql(plan["sql"], db_config, config.max_rows)
                sql_key = (database_id, " ".join(sql.lower().split()))
                if sql_key in seen_sql:
                    continue
                seen_sql.add(sql_key)

                if not valid:
                    validation_errors.append(f"{database_id}: {reason}")
                    sanitized_plans.append(
                        {
                            "database_id": database_id,
                            "purpose": "[blocked by access policy]",
                            "sql": "[blocked by access policy]",
                        }
                    )
                    continue

                executed_plan = {
                    "database_id": database_id,
                    "purpose": plan.get("purpose", ""),
                    "sql": sql,
                }
                sanitized_plans.append(executed_plan)
                rows = registry.execute_readonly(database_id, sql)
                query_results.append(
                    {
                        "database_id": database_id,
                        "sql": sql,
                        "rows": rows,
                        "row_count": len(rows),
                    }
                )
                executed_in_iteration = True

            if iteration >= config.max_sql_iterations - 1 or not executed_in_iteration:
                break

            pending_plans = generate_followup_sql_plans(
                get_llm(),
                state["question"],
                state["schema_context"],
                state.get("memory_context", ""),
                sanitized_plans,
                query_results,
            )
            if not pending_plans:
                break

        return {
            "sql_plans": sanitized_plans,
            "query_results": query_results,
            "validation_errors": validation_errors,
        }

    def answer_node(state: AgentState) -> AgentState:
        if not state.get("query_results"):
            if state.get("validation_errors"):
                return {
                    "answer": (
                        "I cannot answer that because the requested data is blocked by the access "
                        "policy."
                    )
                }
            if state.get("conversation_history") or state.get("memory_context"):
                answer = synthesize_context_answer(
                    get_llm(),
                    state["question"],
                    state.get("original_question", state["question"]),
                    state.get("conversation_history", []),
                    state.get("memory_context", ""),
                )
                return {"answer": answer}
            return {"answer": "The database does not contain enough information to answer that."}

        answer = synthesize_answer(
            get_llm(),
            state["question"],
            state.get("original_question", state["question"]),
            state["query_results"],
            state.get("validation_errors", []),
        )
        return {"answer": answer}

    graph = StateGraph(AgentState)
    graph.add_node("policy", policy_node)
    graph.add_node("contextualize", contextualize_node)
    graph.add_node("route_databases", route_node)
    graph.add_node("load_schema", schema_node)
    graph.add_node("generate_sql", sql_node)
    graph.add_node("execute_sql", execute_node)
    graph.add_node("answer", answer_node)

    graph.add_edge(START, "policy")
    graph.add_conditional_edges("policy", _policy_branch, {"allowed": "contextualize", "blocked": END})
    graph.add_conditional_edges(
        "contextualize",
        _policy_branch,
        {"allowed": "route_databases", "blocked": END},
    )
    graph.add_conditional_edges(
        "route_databases",
        _route_branch,
        {"selected": "load_schema", "no_database": END},
    )
    graph.add_edge("load_schema", "generate_sql")
    graph.add_edge("generate_sql", "execute_sql")
    graph.add_edge("execute_sql", "answer")
    graph.add_edge("answer", END)

    return graph.compile()


def _policy_branch(state: AgentState) -> str:
    return "allowed" if state.get("allowed") else "blocked"


def _route_branch(state: AgentState) -> str:
    return "selected" if state.get("selected_databases") else "no_database"


def run_agent(
    config: AgentConfig,
    question: str,
    catalog: DatabaseCatalog | None = None,
    schema_source: str = "runtime",
    conversation_history: list[dict[str, str]] | None = None,
    memory_context: str = "",
    forced_database_ids: list[str] | None = None,
) -> AgentState:
    app = build_graph(config, catalog=catalog, schema_source=schema_source)
    result = app.invoke(
        {
            "question": question,
            "conversation_history": conversation_history or [],
            "memory_context": memory_context,
            "forced_database_ids": forced_database_ids or [],
        }
    )
    return result

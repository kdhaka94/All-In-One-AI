from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from db_agentic_system.state import QueryResult, SqlPlan

# Overridable indirection so tests can stub out real waiting.
_sleep = time.sleep

# Transient / rate-limit signatures worth retrying (case-insensitive).
_RETRYABLE = re.compile(
    r"429|resource[_ ]?exhausted|rate[ _]?limit|quota|503|overloaded|unavailable|timeout",
    re.IGNORECASE,
)
# Provider-supplied "retry in 46s" / "retryDelay: '46s'" hints.
_DELAY_HINT = re.compile(r"retry(?:[_ ]?delay)?['\":\s]+(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)


def _is_retryable(exc: Exception) -> bool:
    return bool(_RETRYABLE.search(str(exc)))


def _retry_delay_hint(exc: Exception) -> float | None:
    match = _DELAY_HINT.search(str(exc))
    return float(match.group(1)) if match else None


def _invoke(
    llm: BaseChatModel,
    messages: Any,
    *,
    max_attempts: int = 4,
    base_delay: float = 2.0,
    max_delay: float = 20.0,
) -> Any:
    """Invoke a chat model, retrying transient/rate-limit errors with backoff.

    Non-retryable errors propagate immediately. When retries are exhausted on a
    rate-limit error, raise a clear message instead of a raw provider dump.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return llm.invoke(messages)
        except Exception as exc:
            retryable = _is_retryable(exc)
            if attempt >= max_attempts or not retryable:
                if retryable:
                    raise RuntimeError(
                        "The model provider is rate-limited (quota / RESOURCE_EXHAUSTED). "
                        "Wait a minute and retry, or switch to a provider/key with more quota. "
                        f"Last error: {exc}"
                    ) from exc
                raise
            hint = _retry_delay_hint(exc)
            delay = hint if hint is not None else base_delay * (2 ** (attempt - 1))
            _sleep(min(delay, max_delay))


def build_chat_model(provider: str | None = None, model: str | None = None) -> BaseChatModel:
    provider = (provider or os.getenv("DB_AGENT_PROVIDER", "gemini")).lower()
    if provider == "gemini":
        api_key = gemini_api_key()
        if not api_key:
            raise RuntimeError(
                "Missing Gemini credentials. Set GOOGLE_API_KEY or GEMINI_API_KEY before asking "
                "questions that need SQL generation or answer synthesis."
            )
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError as exc:
            raise RuntimeError(
                "Gemini provider requires langchain-google-genai. Run `pip install -e .`."
            ) from exc

        model = model or os.getenv("DB_AGENT_MODEL", "gemini-3.1-flash-lite")
        return ChatGoogleGenerativeAI(model=model, google_api_key=api_key, temperature=0)

    if provider == "openai":
        if not os.getenv("OPENAI_API_KEY") and not os.getenv("OPENAI_ADMIN_KEY"):
            raise RuntimeError(
                "Missing OpenAI credentials. Set OPENAI_API_KEY before asking questions that need "
                "SQL generation or answer synthesis."
            )
        from langchain_openai import ChatOpenAI

        model = model or os.getenv("DB_AGENT_MODEL", "gpt-4.1-mini")
        return ChatOpenAI(model=model, temperature=0)

    raise RuntimeError(f"Unsupported provider {provider!r}. Use 'gemini' or 'openai'.")


def gemini_api_key() -> str | None:
    return os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")


# Selectable model presets for the UI switcher. API keys always come from the
# environment; the request only ever names one of these ids.
MODEL_OPTIONS: list[dict[str, str]] = [
    {
        "id": "gemini:gemini-3.1-flash-lite",
        "label": "Gemini · gemini-3.1-flash-lite",
        "provider": "gemini",
        "model": "gemini-3.1-flash-lite",
        "embedding_model": "models/gemini-embedding-001",
    },
    {
        "id": "openai:gpt-5.4-mini",
        "label": "OpenAI · gpt-5.4-mini",
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "embedding_model": "text-embedding-3-small",
    },
]


def provider_key_configured(provider: str) -> bool:
    provider = provider.lower()
    if provider == "gemini":
        return bool(gemini_api_key())
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_ADMIN_KEY"))
    return False


def available_model_options() -> list[dict[str, str]]:
    """Model presets whose provider has a configured API key."""
    return [option for option in MODEL_OPTIONS if provider_key_configured(option["provider"])]


def resolve_model_option(model_id: str | None) -> dict[str, str] | None:
    if not model_id:
        return None
    return next((option for option in MODEL_OPTIONS if option["id"] == model_id), None)


def default_model_id() -> str | None:
    """The available preset matching the env provider/model, else the first available."""
    options = available_model_options()
    if not options:
        return None
    env_provider = os.getenv("DB_AGENT_PROVIDER", "gemini").lower()
    env_model = os.getenv("DB_AGENT_MODEL")
    for option in options:
        if option["provider"] == env_provider and (env_model is None or option["model"] == env_model):
            return option["id"]
    return options[0]["id"]


def _scope_rule(record_scope: str) -> str:
    """The planner-facing line that pins every generated query to one record."""
    if not record_scope:
        return ""
    return (
        "- RECORD SCOPE (mandatory): "
        + record_scope
        + " A query that is not filtered to that record will be rejected.\n"
    )


def contextualize_question(
    llm: BaseChatModel,
    question: str,
    conversation_history: list[dict[str, str]],
    memory_context: str = "",
) -> str:
    if not conversation_history and not memory_context:
        return question

    history_text = "\n".join(
        f"{turn.get('role', 'unknown')}: {turn.get('content', '')}"
        for turn in conversation_history[-10:]
    )
    response = _invoke(llm,
        [
            SystemMessage(
                content=(
                    "Rewrite the user's latest database question into a standalone question.\n"
                    "Use conversation history and structured memory to resolve references like it, "
                    "they, that, those, the same city, previous result, or that person.\n"
                    "Prefer exact IDs, table names, and primary-key values from structured memory.\n"
                    "If previous SQL results contain rows with IDs, preserve those IDs in the "
                    "standalone question.\n"
                    "When the latest question asks whether anyone or anything else was involved, "
                    "behind, responsible for, related to, caused by, sponsored by, approved by, "
                    "owned by, or connected to a previously identified entity, rewrite it as a "
                    "task to inspect records directly associated with that known entity and then "
                    "identify any other related entities revealed by those records.\n"
                    "When the latest question is a short request such as 'guess', 'estimate', "
                    "'roughly', 'what do you think', or 'your best guess', preserve the immediate "
                    "previous user question's intent. If that prior question asked for certainty, "
                    "confidence, probability, likelihood, percentage, explanation, or reasoning, "
                    "rewrite the latest question as an inferential context question, not as a new "
                    "database lookup for related rows.\n"
                    "Do not turn an unresolved follow-up into a NULL lookup unless the user "
                    "explicitly asks for missing or NULL values.\n"
                    "Do not answer the question.\n"
                    "Do not add facts not present in the latest question or history.\n"
                    "Return only the standalone question."
                )
            ),
            HumanMessage(
                content=(
                    f"Conversation history:\n{history_text}\n\n"
                    f"Structured memory:\n{memory_context}\n\n"
                    f"Latest question:\n{question}"
                )
            ),
        ]
    )
    return _content_to_text(response.content).strip() or question


def generate_sql_plans(
    llm: BaseChatModel,
    question: str,
    schema_context: dict[str, str],
    memory_context: str = "",
    record_scope: str = "",
) -> list[SqlPlan]:
    schema_text = "\n\n---\n\n".join(schema_context.values())
    response = _invoke(llm,
        [
            SystemMessage(
                content=(
                    "You generate safe SQL plans for database-grounded question answering.\n"
                    "Rules:\n"
                    + _scope_rule(record_scope) +
                    "- Return only valid JSON. Do not use markdown, comments, trailing commas, "
                    "or unquoted object keys.\n"
                    "- Generate one or more SELECT queries per relevant database when the question "
                    "requires exploring more than one evidence path.\n"
                    "- Use only tables and columns shown in schema context.\n"
                    "- Use structured memory when present, especially prior IDs, table names, "
                    "primary-key values, and prior SQL result rows.\n"
                    "- Prefer filtering by remembered primary keys over fuzzy name matching.\n"
                    "- For follow-up questions asking whether another related person, account, "
                    "object, event, cause, owner, source, sponsor, approver, or actor exists, "
                    "first inspect records directly connected to the remembered entity or result "
                    "before doing broad keyword searches.\n"
                    "- Text or note columns can contain evidence. If the question asks for a "
                    "hidden related entity and a known entity has related notes, interviews, "
                    "messages, logs, comments, descriptions, or transcripts, retrieve that "
                    "evidence with identifying columns.\n"
                    "- If a question asks for something else involved, behind, responsible, "
                    "related, caused, sponsored, approved, owned, or connected after a known "
                    "entity was identified, include a direct query for evidence records attached "
                    "to that known entity when the schema supports it. Do not rely only on "
                    "re-querying the original event or broad keyword searches.\n"
                    "- If the question asks for a subjective confidence estimate, probability, "
                    "likelihood, percentage sure, or a guess based on already available or prior "
                    "information, return an empty plans list unless the user explicitly asks to "
                    "calculate fresh counts, rates, or records from the database.\n"
                    "- Do not search for NULL unless the user explicitly asks for missing values.\n"
                    "- Never select blocked columns.\n"
                    "- Do not create, update, delete, alter, or drop anything.\n"
                    "- If the schema is insufficient, return an empty plans list.\n"
                )
            ),
            HumanMessage(
                content=(
                    f"Question:\n{question}\n\n"
                    f"Structured memory:\n{memory_context}\n\n"
                    f"Schema context:\n{schema_text}\n\n"
                    "Return JSON with this shape:\n"
                    '{"plans":[{"database_id":"...","purpose":"...","sql":"SELECT ..."}]}'
                )
            ),
        ]
    )
    payload = _parse_json_response(response.content)
    plans = payload.get("plans", [])
    return [
        {"database_id": plan["database_id"], "purpose": plan.get("purpose", ""), "sql": plan["sql"]}
        for plan in plans
        if "database_id" in plan and "sql" in plan
    ]


def generate_followup_sql_plans(
    llm: BaseChatModel,
    question: str,
    schema_context: dict[str, str],
    memory_context: str,
    executed_plans: list[SqlPlan],
    query_results: list[QueryResult],
    record_scope: str = "",
) -> list[SqlPlan]:
    schema_text = "\n\n---\n\n".join(schema_context.values())
    response = _invoke(llm,
        [
            SystemMessage(
                content=(
                    "You decide whether more safe SQL is needed to answer a database question.\n"
                    "Rules:\n"
                    + _scope_rule(record_scope) +
                    "- Return only valid JSON. Do not use markdown, comments, trailing commas, "
                    "or unquoted object keys.\n"
                    "- If the existing query results are enough to answer the question, return an "
                    "empty plans list.\n"
                    "- If existing results reveal new concrete lookup keys, IDs, relationships, "
                    "or filter predicates needed to answer the same question, generate the next "
                    "targeted SELECT query or queries.\n"
                    "- If previous results are empty or unhelpful but the question and memory name "
                    "a concrete entity from the conversation, generate a targeted query for records "
                    "directly related to that entity before giving up.\n"
                    "- For follow-up questions asking whether another related person, account, "
                    "object, event, cause, owner, source, sponsor, approver, or actor exists, "
                    "inspect evidence connected to the known entity, then use any newly revealed "
                    "predicates to search the appropriate tables.\n"
                    "- If previous queries followed the original event, witnesses, logs, or broad "
                    "keywords but did not inspect records directly attached to a known entity named "
                    "in the question or memory, generate that direct entity-evidence query next "
                    "when the schema supports it.\n"
                    "- If the question asks for a subjective confidence estimate, probability, "
                    "likelihood, percentage sure, or a guess based on already available or prior "
                    "information, return an empty plans list unless the user explicitly asks to "
                    "calculate fresh counts, rates, or records from the database.\n"
                    "- Text or note columns can contain evidence. Extract concrete values from "
                    "those rows, such as names, IDs, dates, categories, statuses, attributes, "
                    "measurements, products, locations, events, or counts, and map them to schema "
                    "columns when forming the next SELECT.\n"
                    "- Use only facts present in the question, structured memory, schema context, "
                    "or existing query result rows.\n"
                    "- Use only tables and columns shown in schema context.\n"
                    "- Do not repeat previous SQL.\n"
                    "- Prefer filtering by concrete IDs and values found in previous results.\n"
                    "- Never select blocked columns.\n"
                    "- Do not create, update, delete, alter, or drop anything.\n"
                    "- If no useful next query can be formed, return an empty plans list.\n"
                )
            ),
            HumanMessage(
                content=(
                    f"Question:\n{question}\n\n"
                    f"Structured memory:\n{memory_context}\n\n"
                    f"Schema context:\n{schema_text}\n\n"
                    f"Previous SQL plans:\n{json.dumps(executed_plans, default=str)}\n\n"
                    f"Existing query results JSON:\n{json.dumps(query_results, default=str)}\n\n"
                    "Return JSON with this shape:\n"
                    '{"plans":[{"database_id":"...","purpose":"...","sql":"SELECT ..."}]}'
                )
            ),
        ]
    )
    payload = _parse_json_response(response.content)
    plans = payload.get("plans", [])
    return [
        {"database_id": plan["database_id"], "purpose": plan.get("purpose", ""), "sql": plan["sql"]}
        for plan in plans
        if "database_id" in plan and "sql" in plan
    ]


def generate_repair_sql_plans(
    llm: BaseChatModel,
    question: str,
    schema_context: dict[str, str],
    memory_context: str,
    failed_plans: list[dict[str, str]],
) -> list[SqlPlan]:
    """Given SELECT queries that failed at execution, propose corrected SELECTs.

    ``failed_plans`` items carry the failing ``sql`` and the database ``error`` so the
    model can fix the specific problem (wrong column, bad join, unknown function).
    """
    schema_text = "\n\n---\n\n".join(schema_context.values())
    response = _invoke(llm,
        [
            SystemMessage(
                content=(
                    "You repair SELECT queries that failed to run against the database.\n"
                    "Rules:\n"
                    "- Return only valid JSON. Do not use markdown, comments, trailing commas, "
                    "or unquoted object keys.\n"
                    "- Each failed query includes the database error message. Fix the specific "
                    "cause: a wrong or missing column, a bad join, an unknown function, or a "
                    "syntax problem.\n"
                    "- Use only tables and columns shown in schema context. If the query used a "
                    "column that is not in the schema, replace it with the correct one or drop it.\n"
                    "- Keep the original intent (purpose) of each query.\n"
                    "- Do not repeat a query verbatim if it failed; change what caused the error.\n"
                    "- Only SELECT statements. Do not create, update, delete, alter, or drop "
                    "anything, and never select blocked columns.\n"
                    "- If a failed query cannot be repaired from the shown schema, omit it.\n"
                    "- If nothing can be repaired, return an empty plans list.\n"
                )
            ),
            HumanMessage(
                content=(
                    f"Question:\n{question}\n\n"
                    f"Structured memory:\n{memory_context}\n\n"
                    f"Schema context:\n{schema_text}\n\n"
                    f"Failed queries and their database errors JSON:\n"
                    f"{json.dumps(failed_plans, default=str)}\n\n"
                    "Return JSON with this shape:\n"
                    '{"plans":[{"database_id":"...","purpose":"...","sql":"SELECT ..."}]}'
                )
            ),
        ]
    )
    payload = _parse_json_response(response.content)
    plans = payload.get("plans", [])
    return [
        {"database_id": plan["database_id"], "purpose": plan.get("purpose", ""), "sql": plan["sql"]}
        for plan in plans
        if "database_id" in plan and "sql" in plan
    ]


def synthesize_answer(
    llm: BaseChatModel,
    question: str,
    original_question: str,
    results: list[QueryResult],
    validation_errors: list[str],
) -> str:
    response = _invoke(llm,
        [
            SystemMessage(
                content=(
                    "You are a database-grounded assistant.\n"
                    "Answer using ONLY the provided database query results.\n"
                    "Do not use outside knowledge.\n"
                    "Do not invent numbers, names, dates, totals, IDs, or explanations.\n"
                    "Write a clear, human-readable answer in plain business language.\n"
                    "Never show raw database column names (for example "
                    "interest_period_frequency_enum, account_balance_derived, status_enum) or raw "
                    "enum/status integer codes in your answer. Translate them into natural wording, "
                    "or omit a value you cannot express in plain language. Do not dump a "
                    "'column_name: value' list.\n"
                    "Format numbers readably (for example 2%, 1,200) and drop meaningless trailing "
                    "zeros.\n"
                    "Distinguish roles carefully. Do not describe a person, account, object, or "
                    "event as involved, responsible, causal, suspicious, approved, owned, or "
                    "connected merely because it appears in context; use the role shown by the "
                    "query results.\n"
                    "If records mention witnesses, observers, references, examples, or unrelated "
                    "text matches, do not present them as perpetrators, owners, sponsors, causes, "
                    "or responsible parties unless the result rows support that role.\n"
                    "If the results are empty, say the database has no matching records.\n"
                    "If the results are insufficient, say the database does not contain enough information.\n"
                    "Mention validation failures only if they prevent answering."
                )
            ),
            HumanMessage(
                content=(
                    f"Original user question:\n{original_question}\n\n"
                    f"Standalone database question:\n{question}\n\n"
                    f"Query results JSON:\n{json.dumps(results, default=str)}\n\n"
                    f"Validation errors:\n{json.dumps(validation_errors)}"
                )
            ),
        ]
    )
    return _content_to_text(response.content)


def synthesize_brief(
    llm: BaseChatModel,
    question: str,
    record_label: str,
    evidence: list[dict[str, Any]],
) -> str:
    """Write an operations brief about one record, citing the evidence behind each claim.

    Every claim has to carry a ``[e1]`` marker naming the query it came from, so
    the screen can turn each one into a link back to the SQL and rows that support
    it. An uncited brief is just prose: the citation is the point.
    """
    response = _invoke(llm,
        [
            SystemMessage(
                content=(
                    "You write short operations briefs about ONE record for a banking "
                    "operations team.\n"
                    "Use ONLY the numbered evidence provided. Do not use outside knowledge and "
                    "do not invent numbers, dates, names, statuses, or totals.\n"
                    "Cite evidence inline with its marker in square brackets, for example "
                    "[e1]. Every factual claim must carry at least one citation, placed "
                    "immediately after the claim.\n"
                    "Only cite markers that appear in the evidence list.\n"
                    "Structure the brief as a one-paragraph summary followed by short bullet "
                    "lines for anything that needs attention. Keep it under 200 words.\n"
                    "Write plain business language. Never show raw column names or raw enum "
                    "integer codes; translate them or leave them out.\n"
                    "If the evidence is empty or does not support a conclusion, say plainly "
                    "that there is not enough evidence about this record, and cite nothing."
                )
            ),
            HumanMessage(
                content=(
                    f"Record under investigation:\n{record_label}\n\n"
                    f"Investigation question:\n{question}\n\n"
                    f"Evidence JSON:\n{json.dumps(evidence, default=str)}"
                )
            ),
        ]
    )
    return _content_to_text(response.content)


def synthesize_context_answer(
    llm: BaseChatModel,
    question: str,
    original_question: str,
    conversation_history: list[dict[str, str]],
    memory_context: str,
) -> str:
    history_text = "\n".join(
        f"{turn.get('role', 'unknown')}: {turn.get('content', '')}"
        for turn in conversation_history[-12:]
    )
    response = _invoke(llm,
        [
            SystemMessage(
                content=(
                    "You answer database-chat follow-ups from conversation context when no new "
                    "SQL query is needed or available.\n"
                    "Use only the provided conversation history and structured memory. Do not use "
                    "outside knowledge.\n"
                    "This path is appropriate for explanations, reasoning from already retrieved "
                    "facts, confidence estimates, uncertainty, summaries, and explicit requests to "
                    "guess based on available information.\n"
                    "If the user asks for a numeric confidence or percentage, provide a clearly "
                    "labeled estimate and explain the evidence and uncertainty. Do not imply the "
                    "database stores that percentage.\n"
                    "If the question requires fresh database facts that are not in context, say "
                    "the available context is not enough to answer.\n"
                    "Do not say 'the database does not contain enough information' for subjective "
                    "estimates or guesses; instead explain that the estimate is inferential."
                )
            ),
            HumanMessage(
                content=(
                    f"Original user question:\n{original_question}\n\n"
                    f"Standalone question:\n{question}\n\n"
                    f"Conversation history:\n{history_text}\n\n"
                    f"Structured memory:\n{memory_context}"
                )
            ),
        ]
    )
    return _content_to_text(response.content)


def _parse_json_response(content: Any) -> dict[str, Any]:
    text = _content_to_text(content).strip()
    if text.startswith("```"):
        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def _content_to_text(content: Any) -> str:
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and "text" in part:
                parts.append(str(part["text"]))
            else:
                parts.append(str(part))
        return "\n".join(parts)
    return str(content)

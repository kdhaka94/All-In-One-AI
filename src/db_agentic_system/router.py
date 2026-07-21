from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Protocol

from db_agentic_system.config import DatabaseConfig
from db_agentic_system.state import SelectedDatabase


class Embedder(Protocol):
    def embed_query(self, text: str) -> list[float]:
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...


@dataclass
class SemanticDatabaseRouter:
    databases: list[DatabaseConfig]
    max_selected: int = 2
    min_score: float = 0.08
    embedder: Embedder | None = None
    catalog_text_by_database_id: dict[str, str] | None = None

    def route(self, question: str) -> list[SelectedDatabase]:
        if self.embedder is not None:
            return self._embedding_route(question)
        return self._lexical_route(question)

    def _embedding_route(self, question: str) -> list[SelectedDatabase]:
        catalog_texts = [self._catalog_text(db) for db in self.databases]
        question_vector = self.embedder.embed_query(question)
        db_vectors = self.embedder.embed_documents(catalog_texts)

        scored = []
        for db, vector in zip(self.databases, db_vectors, strict=True):
            score = cosine_similarity(question_vector, vector)
            scored.append((score, db))

        return self._format_selected(scored, "semantic metadata match")

    def _lexical_route(self, question: str) -> list[SelectedDatabase]:
        query_terms = set(tokenize(question))
        scored = []
        for db in self.databases:
            terms = set(tokenize(self._catalog_text(db)))
            score = len(query_terms & terms) / max(len(query_terms), 1)
            scored.append((score, db))

        return self._format_selected(scored, "keyword metadata match")

    def _catalog_text(self, db: DatabaseConfig) -> str:
        if self.catalog_text_by_database_id and db.id in self.catalog_text_by_database_id:
            return self.catalog_text_by_database_id[db.id]
        return catalog_text(db)

    def _format_selected(
        self, scored: list[tuple[float, DatabaseConfig]], reason: str
    ) -> list[SelectedDatabase]:
        selected = sorted(scored, key=lambda item: item[0], reverse=True)[: self.max_selected]
        formatted: list[SelectedDatabase] = [
            {
                "id": db.id,
                "name": db.name,
                "reason": reason,
                "score": round(float(score), 4),
            }
            for score, db in selected
            if score >= self.min_score
        ]
        if not formatted and len(self.databases) == 1:
            db = self.databases[0]
            return [
                {
                    "id": db.id,
                    "name": db.name,
                    "reason": "only configured database",
                    "score": 0.0,
                }
            ]
        return formatted


def build_embedder(
    provider: str | None = None, embedding_model: str | None = None
) -> Embedder | None:
    provider = (provider or os.getenv("DB_AGENT_PROVIDER", "gemini")).lower()
    if provider == "gemini":
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            return None
        try:
            from langchain_google_genai import GoogleGenerativeAIEmbeddings
        except ImportError:
            return None

        model = embedding_model or os.getenv("DB_AGENT_EMBEDDING_MODEL", "models/gemini-embedding-001")
        return GoogleGenerativeAIEmbeddings(model=model, google_api_key=api_key)

    if provider == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            return None
        try:
            from langchain_openai import OpenAIEmbeddings
        except ImportError:
            return None

        model = embedding_model or os.getenv("DB_AGENT_EMBEDDING_MODEL", "text-embedding-3-small")
        return OpenAIEmbeddings(model=model)

    return None


def catalog_text(db: DatabaseConfig) -> str:
    return "\n".join(
        [
            db.id,
            db.name,
            db.description,
            "tables: " + ", ".join(db.include_tables),
            "blocked columns: " + ", ".join(db.blocked_columns),
        ]
    )


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9_]+", text.lower())


def cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)

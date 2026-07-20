from __future__ import annotations

import re
from dataclasses import dataclass

from db_agentic_system.catalog import DatabaseProfile, TableProfile
from db_agentic_system.router import Embedder, cosine_similarity

_VECTOR_CACHE: dict[tuple[int, str, str], tuple[list[str], list[list[float]]]] = {}


def _table_text(table: TableProfile) -> str:
    parts = [table.name]
    if table.description:
        parts.append(table.description)
    parts.append(" ".join(column.name for column in table.columns))
    return " ".join(parts)


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9_]+", text.lower()))


@dataclass
class TableSelector:
    max_tables: int | None
    embedder: Embedder | None = None
    fk_neighbor_cap: int = 10

    def select(self, question: str, profile: DatabaseProfile) -> list[str]:
        tables = profile.tables
        if self.max_tables is None or len(tables) <= self.max_tables:
            return [table.name for table in tables]

        scored = self._scores(question, profile)
        ranked = sorted(zip(scored, tables), key=lambda item: item[0], reverse=True)
        chosen = [table.name for _, table in ranked[: self.max_tables]]

        chosen_set = set(chosen)
        by_name = {table.name for table in tables}
        added = 0
        for name in list(chosen):
            table = next(table for table in tables if table.name == name)
            for foreign_key in table.foreign_keys:
                neighbor = foreign_key.referred_table
                if neighbor and neighbor in by_name and neighbor not in chosen_set:
                    chosen.append(neighbor)
                    chosen_set.add(neighbor)
                    added += 1
                    if added >= self.fk_neighbor_cap:
                        return chosen
        return chosen

    def _scores(self, question: str, profile: DatabaseProfile) -> list[float]:
        if self.embedder is not None:
            names, vectors = self._table_vectors(profile)
            question_vector = self.embedder.embed_query(question)
            return [cosine_similarity(question_vector, vector) for vector in vectors]
        query_terms = _tokenize(question)
        return [
            len(query_terms & _tokenize(_table_text(table))) / max(len(query_terms), 1)
            for table in profile.tables
        ]

    def _table_vectors(self, profile: DatabaseProfile) -> tuple[list[str], list[list[float]]]:
        assert self.embedder is not None
        key = (id(self.embedder), profile.id, profile.learned_at)
        cached = _VECTOR_CACHE.get(key)
        current_names = [table.name for table in profile.tables]
        if cached is not None and cached[0] == current_names:
            return cached
        vectors = self.embedder.embed_documents([_table_text(table) for table in profile.tables])
        _VECTOR_CACHE[key] = (current_names, vectors)
        return current_names, vectors

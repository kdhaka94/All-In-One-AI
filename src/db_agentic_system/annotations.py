from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from db_agentic_system.catalog import DatabaseCatalog


class TableAnnotation(BaseModel):
    description: str | None = None
    columns: dict[str, str] = Field(default_factory=dict)


class AnnotationOverlay(BaseModel):
    tables: dict[str, TableAnnotation] = Field(default_factory=dict)


def load_annotations(path: str | Path) -> AnnotationOverlay:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return AnnotationOverlay.model_validate(raw)


def merge_annotations(catalog: DatabaseCatalog, overlay: AnnotationOverlay) -> DatabaseCatalog:
    tables_by_name = {name.lower(): annotation for name, annotation in overlay.tables.items()}
    merged = catalog.model_copy(deep=True)
    for profile in merged.databases:
        for table in profile.tables:
            annotation = tables_by_name.get(table.name.lower())
            if annotation is None:
                continue
            if annotation.description:
                table.description = annotation.description
            columns_by_name = {c.lower(): text for c, text in annotation.columns.items()}
            for column in table.columns:
                text = columns_by_name.get(column.name.lower())
                if text:
                    column.description = text
    return merged

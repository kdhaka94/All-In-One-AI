from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from db_agentic_system.catalog import DatabaseProfile


def enrich_enums(profile: DatabaseProfile, engine: Engine) -> DatabaseProfile:
    if "r_enum_value" not in inspect(engine).get_table_names():
        return profile

    legend: dict[str, dict[str, str]] = {}
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT enum_name, enum_id, enum_message_property FROM r_enum_value")
        )
        for enum_name, enum_id, message in rows:
            # Coerce to str so a NULL label can never break the dict[str, str] contract
            # (which would otherwise make the whole catalog fail to load).
            label = "" if message is None else str(message)
            legend.setdefault(str(enum_name), {})[str(enum_id)] = label

    for table in profile.tables:
        for column in table.columns:
            values = legend.get(column.name)
            if values:
                column.enum_values = values
    return profile

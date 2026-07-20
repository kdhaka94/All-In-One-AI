from db_agentic_system.annotations import AnnotationOverlay, load_annotations, merge_annotations
from db_agentic_system.catalog import ColumnProfile, DatabaseCatalog, DatabaseProfile, TableProfile


def _catalog() -> DatabaseCatalog:
    return DatabaseCatalog(
        databases=[
            DatabaseProfile(
                id="fineract",
                name="Fineract",
                description="Core banking.",
                dialect="postgres",
                learned_at="2026-07-21T00:00:00+00:00",
                tables=[
                    TableProfile(
                        name="m_loan",
                        description="AI: some loan table.",
                        columns=[ColumnProfile(name="loan_status_id", type="SMALLINT")],
                    )
                ],
            )
        ]
    )


def test_overlay_overrides_table_and_column_descriptions() -> None:
    overlay = AnnotationOverlay.model_validate(
        {
            "tables": {
                "m_loan": {
                    "description": "Human: the loan account.",
                    "columns": {"loan_status_id": "Human: loan lifecycle status."},
                }
            }
        }
    )
    merged = merge_annotations(_catalog(), overlay)
    table = merged.databases[0].tables[0]
    assert table.description == "Human: the loan account."
    assert table.columns[0].description == "Human: loan lifecycle status."


def test_load_annotations_reads_yaml(tmp_path) -> None:
    path = tmp_path / "ann.yaml"
    path.write_text(
        "tables:\n  m_loan:\n    description: The loan account.\n"
    )
    overlay = load_annotations(path)
    assert overlay.tables["m_loan"].description == "The loan account."


def test_merge_is_noop_for_unknown_tables() -> None:
    overlay = AnnotationOverlay.model_validate({"tables": {"nope": {"description": "x"}}})
    merged = merge_annotations(_catalog(), overlay)
    assert merged.databases[0].tables[0].description == "AI: some loan table."


def test_merge_does_not_mutate_input_catalog() -> None:
    original = _catalog()
    overlay = AnnotationOverlay.model_validate(
        {"tables": {"m_loan": {"description": "Human: the loan account.",
                               "columns": {"loan_status_id": "Human: status."}}}}
    )
    merge_annotations(original, overlay)
    assert original.databases[0].tables[0].description == "AI: some loan table."
    assert original.databases[0].tables[0].columns[0].description is None


def test_matched_table_without_description_keeps_existing() -> None:
    catalog = _catalog()
    overlay = AnnotationOverlay.model_validate(
        {"tables": {"m_loan": {"columns": {"loan_status_id": "Human: status."}}}}
    )
    merged = merge_annotations(catalog, overlay)
    table = merged.databases[0].tables[0]
    assert table.description == "AI: some loan table."          # unchanged
    assert table.columns[0].description == "Human: status."      # applied

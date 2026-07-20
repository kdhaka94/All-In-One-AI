from pathlib import Path

from db_agentic_system.config import load_config


def test_config_parses_table_selection_and_annotations_fields(tmp_path: Path) -> None:
    config_file = tmp_path / "cfg.yaml"
    config_file.write_text(
        """
max_selected_tables: 25
annotations_path: config/fineract_annotations.yaml
databases:
  - id: fineract
    name: Fineract
    description: Core banking.
    uri: postgresql+psycopg://u:p@localhost:5432/fineract_default
    dialect: postgres
    include_tables: []
"""
    )
    config = load_config(config_file)
    assert config.max_selected_tables == 25
    assert config.annotations_path == "config/fineract_annotations.yaml"
    assert config.databases[0].include_tables == []


def test_config_defaults_keep_today_behavior(tmp_path: Path) -> None:
    config_file = tmp_path / "cfg.yaml"
    config_file.write_text(
        """
databases:
  - id: x
    name: X
    description: d
    uri: sqlite:///x.db
"""
    )
    config = load_config(config_file)
    assert config.max_selected_tables is None
    assert config.annotations_path is None

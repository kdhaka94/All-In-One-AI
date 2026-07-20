from __future__ import annotations

import argparse
import sys

from db_agentic_system.catalog import build_catalog, load_catalog, save_catalog
from db_agentic_system.config import load_config
from db_agentic_system.graph import run_agent
from db_agentic_system.router import SemanticDatabaseRouter


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] not in {"ask", "index", "compare", "-h", "--help"}:
        _legacy_ask()
        return

    parser = argparse.ArgumentParser(description="Grounded Q&A across multiple databases.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask = subparsers.add_parser("ask", help="Ask a grounded question.")
    ask.add_argument("question", help="Question to answer from configured databases.")
    ask.add_argument("--config", default="config/databases.yaml", help="Path to database config YAML.")
    ask.add_argument("--catalog", help="Optional learned catalog JSON path.")
    ask.add_argument(
        "--schema-source",
        choices=["learned", "runtime"],
        default="runtime",
        help="Use learned catalog schema or inspect schema at runtime.",
    )

    index = subparsers.add_parser("index", help="Learn database schemas into a catalog JSON file.")
    index.add_argument("--config", default="config/databases.yaml", help="Path to database config YAML.")
    index.add_argument("--output", default="config/database_catalog.json", help="Output catalog path.")
    index.add_argument(
        "--annotate",
        action="store_true",
        help="Run an LLM pass to fill table/column descriptions.",
    )

    compare = subparsers.add_parser(
        "compare",
        help="Compare learned-catalog routing with runtime-config routing for a question.",
    )
    compare.add_argument("question", help="Question to route.")
    compare.add_argument("--config", default="config/databases.yaml", help="Path to database config YAML.")
    compare.add_argument("--catalog", default="config/database_catalog.json", help="Catalog JSON path.")

    args = parser.parse_args()

    if args.command == "index":
        config = load_config(args.config)
        catalog = build_catalog(config)
        if args.annotate:
            from db_agentic_system.annotate import annotate_catalog
            from db_agentic_system.llm import build_chat_model

            catalog = annotate_catalog(catalog, build_chat_model())
        save_catalog(catalog, args.output)
        print(f"Learned {len(catalog.databases)} database(s) into {args.output}")
        return

    if args.command == "compare":
        config = load_config(args.config)
        catalog = load_catalog(args.catalog)
        learned_texts = {
            profile.id: "\n".join(
                [
                    profile.id,
                    profile.name,
                    profile.description,
                    " ".join(table.name for table in profile.tables),
                    " ".join(column.name for table in profile.tables for column in table.columns),
                ]
            )
            for profile in catalog.databases
        }
        learned_router = SemanticDatabaseRouter(
            config.databases,
            max_selected=config.max_selected_databases,
            min_score=config.min_route_score,
            catalog_text_by_database_id=learned_texts,
        )
        runtime_router = SemanticDatabaseRouter(
            config.databases,
            max_selected=config.max_selected_databases,
            min_score=config.min_route_score,
        )
        print("learned:", learned_router.route(args.question))
        print("runtime:", runtime_router.route(args.question))
        return

    config = load_config(args.config)
    catalog = load_catalog(args.catalog) if args.catalog else None
    result = run_agent(config, args.question, catalog=catalog, schema_source=args.schema_source)
    print(result.get("answer", "No answer produced."))


def _legacy_ask() -> None:
    parser = argparse.ArgumentParser(description="Ask grounded questions across multiple databases.")
    parser.add_argument("question", help="Question to answer from configured databases.")
    parser.add_argument("--config", default="config/databases.yaml", help="Path to database config YAML.")
    args = parser.parse_args()

    config = load_config(args.config)
    result = run_agent(config, args.question)
    print(result.get("answer", "No answer produced."))


if __name__ == "__main__":
    main()

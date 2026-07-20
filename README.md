# Multi-Database Grounded Agent

Starter architecture for a LangGraph/LangChain agent that answers user questions from one or more databases without hallucinating facts.

The design is intentionally split into guarded stages:

```text
question
  -> policy check
  -> semantic database routing
  -> schema loading for selected databases
  -> SQL generation
  -> SQL validation
  -> read-only database execution
  -> grounded answer synthesis
```

## Why this shape

- The model does not answer factual questions from memory.
- The router chooses relevant databases from metadata before SQL is generated, with a relevance threshold to avoid unnecessary database access.
- Each generated SQL statement is validated before execution.
- Only `SELECT`-style read queries are allowed.
- The final answer is instructed to use only the executed query results.

LangGraph is a good fit here because its graph model is built around shared state, nodes, and edges. The official LangGraph docs describe graph workflows as state plus nodes plus edges, and `StateGraph` nodes operate by reading state and returning partial state updates.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
cp config/databases.example.yaml config/databases.yaml
```

Fill in `.env`, then point each database entry to an environment variable containing its SQLAlchemy URL.

Gemini is the default model provider:

```bash
DB_AGENT_PROVIDER=gemini
GOOGLE_API_KEY=your_gemini_key
DB_AGENT_MODEL=gemini-3.1-flash-lite
DB_AGENT_EMBEDDING_MODEL=models/gemini-embedding-001
```

You can put those values in `.env`, or export them in your shell. You can also use `GEMINI_API_KEY` instead of `GOOGLE_API_KEY`.

For a local demo:

```bash
python3 examples/seed_demo.py
export DATABASE_URL_CUSTOMERS=sqlite:///examples/customers.db
export DATABASE_URL_ORDERS=sqlite:///examples/orders.db
cp config/databases.example.yaml config/databases.yaml
db-agent --config config/databases.yaml "How many completed orders are there?"
```

## Configure databases

Edit `config/databases.yaml`:

```yaml
databases:
  - id: customers
    name: Customer Database
    description: Customer profiles, account status, locations, support ownership.
    uri_env: DATABASE_URL_CUSTOMERS
    dialect: postgres
    include_tables:
      - customers
      - accounts
    blocked_tables:
      - internal_admin_notes
    blocked_columns:
      - password_hash
      - ssn
    auto_block_sensitive_columns: true
```

Use descriptions that explain business meaning, not just table names. The router uses this catalog to decide where a question belongs.

Use `blocked_tables` for tables the agent must never see or query. This is useful for answer keys, internal audit tables, admin-only metadata, or staging tables. Blocked tables are excluded from learned catalogs, hidden from runtime schema context, and rejected by SQL validation even if the model tries to query them.

You can also tune:

```yaml
max_selected_databases: 2
min_route_score: 0.08
max_rows: 100
max_sql_iterations: 3
```

`max_sql_iterations` controls bounded follow-up querying. A value above `1` lets the
agent run another targeted read-only query when the first result reveals concrete
IDs, relationships, or filter values needed to answer the same question.

## Run

```bash
db-agent --config config/databases.yaml "How many active customers signed up last month?"
```

The old single-command form still works, but the explicit form is:

```bash
db-agent ask --config config/databases.yaml "How many active customers signed up last month?"
```

## Learn First Vs Runtime

For large or unfamiliar databases, use a first-time learning step:

```bash
db-agent index --config config/databases.yaml --output config/database_catalog.json
db-agent ask --config config/databases.yaml --catalog config/database_catalog.json --schema-source learned "Your question"
```

This stores table names, column names, types, foreign keys, and safe sample rows in a reusable catalog. It is still database/table agnostic: if `include_tables` is empty, the indexer learns every table it can inspect.

By default, common sensitive column names such as passwords, tokens, API keys, credit-card fields, private keys, and SSNs are automatically excluded from learned schema context and rejected during SQL validation. You can keep explicit `blocked_columns` for business-specific fields.

You can compare learned routing against runtime config-only routing without calling an LLM:

```bash
db-agent compare --config config/databases.yaml --catalog config/database_catalog.json "Your question"
```

Use `--schema-source runtime` when you want the agent to inspect the selected database live before generating SQL:

```bash
db-agent ask --config config/databases.yaml --catalog config/database_catalog.json --schema-source runtime "Your question"
```

In practice, the best default is usually learned routing plus runtime verification: route from the catalog for speed and broad table awareness, then refresh/check the selected schema at runtime when accuracy matters.

## Example Unknown SQLite Database

This repo includes [config/murder_mystery.example.yaml](/Users/kuldeepdhaka/Documents/All-In-One-AI/config/murder_mystery.example.yaml), which points at `/Users/kuldeepdhaka/Downloads/sql-murder-mystery.db` as a generic test database.

```bash
db-agent index --config config/murder_mystery.example.yaml --output config/murder_mystery_catalog.json
db-agent compare --config config/murder_mystery.example.yaml --catalog config/murder_mystery_catalog.json "Who was murdered?"
```

## Example: Apache Fineract (full core-banking schema)

[config/fineract.example.yaml](config/fineract.example.yaml) points the agent, **read-only**, at a live [Apache Fineract](https://fineract.apache.org/) core-banking database — the full ~285-table production schema. It exercises three capabilities the toy databases don't:

- **Full-schema table selection.** `include_tables: []` exposes every table, and `max_selected_tables: 25` narrows to the tables relevant to each question before SQL generation, so the model never sees all 285 tables at once. For a schema this large, `table_selection_embeddings: false` ranks tables lexically (table names + annotations carry the domain words) to avoid embedding every table per query.
- **A semantic annotation layer.** [config/fineract_annotations.yaml](config/fineract_annotations.yaml) is a hand-editable overlay of table/column descriptions applied at catalog load — edit it and restart the UI, no re-index needed. An optional `db-agent index --annotate` pass fills descriptions for the remaining tables with the LLM.
- **Deterministic enum decoding.** Fineract stores integer status codes (e.g. `m_loan.loan_status_id`); indexing reads Fineract's own `r_enum_value` table and attaches real labels (`300 = Active`, `600 = Closed`, …) so the agent filters correctly.

Bring up the stack, seed data, create a read-only role, and index — see [fineract/README.md](fineract/README.md) for the full runbook:

```bash
docker compose -f fineract/docker-compose.yml up -d          # Fineract + Postgres (host 5433)
# wait for https://localhost:8443/fineract-provider/actuator/health to report UP
pip install -e ".[seed]" && python fineract/seed_fineract.py  # clients, loans, savings, transactions
docker compose -f fineract/docker-compose.yml exec -T db psql -U postgres -f - < fineract/create-readonly-role.sql
db-agent index --config config/fineract.example.yaml --output config/fineract_catalog.json
```

Then set `DATABASE_URL_FINERACT` in `.env` (see `.env.example`) and pick **"Fineract Core Banking · Learned catalog"** in the Web UI. The agent connects as the SELECT-only `agent_ro` role; the heavy Fineract Java app is only needed for seeding — day-to-day queries need just the Postgres container.

## Web UI

Start the local UI:

```bash
source .venv/bin/activate
export DB_AGENT_PROVIDER=gemini
export GOOGLE_API_KEY=your_gemini_key
db-agent-ui
```

Then open:

[http://127.0.0.1:8000](http://127.0.0.1:8000)

The UI keeps an in-memory chat session. Follow-up questions are rewritten into standalone database questions using the previous turns, then routed through the same policy, database selection, SQL validation, and grounded answer flow.

The session also keeps hidden structured memory from recent traces:

- standalone question
- selected database
- generated SQL
- result previews
- likely row IDs / entity IDs
- facts derived from query results

That hidden memory is used for follow-ups like “what did they say?”, “what was it?”, or “show the previous person’s orders,” where the visible answer may not contain enough IDs to query another table.

The current UI stores this memory in process memory. [db/agent_memory.sql](/Users/kuldeepdhaka/Documents/All-In-One-AI/db/agent_memory.sql) defines the generic tables for a persistent store: sessions, turns, SQL results, entities, facts, and step events.

The right panel shows the trace for the latest answer:

- standalone question
- selected database
- generated SQL
- validation errors
- result preview

While a request is running, the right panel streams progress steps so the UI does not look stuck.

## Production notes

- Put the database user in read-only mode at the database permission level.
- Keep blocked columns in config, but also enforce them with database views where possible.
- Add human approval before allowing broad cross-database joins or high-cardinality exports.
- Log generated SQL, selected databases, row counts, and refusal reasons.
- Add test cases for every sensitive policy rule.

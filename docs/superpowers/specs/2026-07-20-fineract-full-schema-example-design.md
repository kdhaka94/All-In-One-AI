# Apache Fineract as a Full-Schema Example — Design

Date: 2026-07-20
Status: Approved (design), pending implementation plan

## Goal

Run Apache Fineract locally (Dockerized, PostgreSQL-backed), seed it with realistic
banking data via its REST API, and register it as a new **"Fineract Core Banking"**
example in the multi-database grounded agent. The agent queries Fineract's underlying
`fineract_default` Postgres database **read-only**, exposing the **full ~250–300 table
production schema** so we can test the agent against a real core-banking system.

Because a fresh Fineract install has no business data and its full schema is far larger
than the existing toy SQLite examples, two capabilities are required beyond a plain
config entry:

1. **Seed data** — realistic offices, staff, clients, loan/savings products, active
   loans, savings accounts, and transactions.
2. **A table-selection stage** — so ~300 tables do not get dumped into every SQL-gen
   prompt.
3. **A semantic annotation layer** — table/column descriptions (AI-generated + hand-
   editable) and deterministic enum decoding, so the model understands Fineract's cryptic,
   enum-coded schema.

## Decisions (locked)

- **Integration = DB-direct, read-only.** The agent's pipeline is SQL-based
  (router → schema context → SQL gen → sqlglot validation → read-only execute), so we
  point SQLAlchemy at Fineract's Postgres DB. The REST API is used only for seeding, not
  for querying.
- **Backend = PostgreSQL** (Fineract's official Docker Compose default; clean SQLAlchemy
  support via `psycopg`).
- **Full schema** — `include_tables: []` (empty exposes all tables **and** disables the
  whitelist guard).
- **Scale strategy = Level B** — full tables **plus** a per-question table-selection
  stage that feeds only the relevant ~15–25 tables to the model.
- **Realistic seed data** via the REST API.
- **Additive** — the Bank SQLite example stays the default; Fineract is a new selectable
  example.
- **Semantic annotation layer** — tables/columns carry human-readable descriptions,
  produced by an **opt-in AI auto-annotation pass** and overridable by a **hand-editable
  YAML overlay** (user text always wins). Descriptions feed both table-selection and
  SQL generation.
- **Deterministic enum decoding** — coded columns (e.g. `loan_status_id`) get real labels
  read from Fineract's own `r_enum_value` (and optionally `m_code_value`) at index time —
  no LLM guessing of codes.

## Why the table-selection stage is necessary (grounded in the code)

- `graph.py:114` `schema_node` builds a text blob of the selected DB's **entire** schema
  (every table, every column, sample rows) and `sql_node` (`graph.py:118`) injects that
  whole blob into the SQL-generation prompt. At ~300 tables this is ~30k–60k tokens on
  **every** query → slow, costly, and lower SQL accuracy (finding the right 3 tables
  among 300).
- `guards.py:56` shows `include_tables` is a **security whitelist**, not a display
  filter: a non-empty list rejects any query touching a table outside it. "Full tables"
  therefore means `include_tables: []`.
- `guards.py:32,72` already pass `db_config.dialect` to sqlglot, and sqlglot understands
  `"postgres"`. So SQL parsing, read-only enforcement, and LIMIT injection need **no**
  changes for Postgres.

## Architecture

### 1. Infra & seed (`fineract/`)

```
fineract/
  docker-compose.yml     # Fineract app + PostgreSQL. Exact image tags / env vars pulled
                         #   from Fineract's official Postgres compose at build time.
  init-readonly.sql      # Creates SELECT-only role `agent_ro` on fineract_default.
  seed_fineract.py       # Drives the REST API to create realistic data.
  README.md              # Bring-up + seed + index runbook.
```

- **Seed flow** (`seed_fineract.py`): wait for Fineract to boot, then against
  `https://localhost:8443/fineract-provider/api/v1` (basic auth `mifos:password`, header
  `Fineract-Platform-TenantId: default`, TLS verify off for the self-signed cert):
  office + loan officer → clients (activated) → one loan product + one savings product →
  per client: apply/approve/disburse loans and open/approve/activate/fund savings, with
  repayments and deposits. This populates `m_loan`, `m_loan_transaction`,
  `m_savings_account`, `m_savings_account_transaction`, etc.
- **Read-only access:** the agent connects as `agent_ro` (SELECT-only), not the Fineract
  owner — defense-in-depth on top of the existing read-only SQL guard.

### 2. Config

- `config/fineract.example.yaml`:
  - `include_tables: []` (all tables, whitelist off)
  - `blocked_tables: []`
  - `dialect: postgres`
  - `sample_rows: 0` (keeps the ~300-table catalog lean; avoids 300 sampling SELECTs at
    index time)
  - `auto_block_sensitive_columns: true` (still hides `password`/secret columns by name)
  - `uri_env: DATABASE_URL_FINERACT`
  - top-level `max_selected_tables: 25`, `catalog_path: config/fineract_catalog.json`,
    `annotations_path: config/fineract_annotations.yaml`, `max_selected_databases: 1`
- `config/fineract_catalog.json`: generated by
  `db-agent index --config config/fineract.example.yaml --output config/fineract_catalog.json --annotate`.
- `config/fineract_annotations.yaml`: hand-editable description overlay (see §4).

### 3. Table-selection stage (the real code change)

- **New module `src/db_agentic_system/table_selection.py`** — `TableSelector`:
  - Builds a per-table text from the catalog: `table_name` + **table description** +
    `col1, col2, ...` (descriptions from §4 sharpen ranking recall on cryptic names).
  - Ranks tables against the question using the existing `build_embedder()` — one batched
    `embed_documents` over per-table texts + one `embed_query`; **lexical fallback** (token
    overlap, mirroring `router.py`) when no embedder is available.
  - Selects top-K (`max_selected_tables`), then **adds FK-referenced neighbor tables**
    (from the catalog's `foreign_keys`) up to a cap, so multi-table joins stay correct.
  - **Module-level cache** of per-table vectors keyed by catalog identity
    (`database_id` + `learned_at`), so the long-lived web server embeds tables once rather
    than per query. CLI pays one batched embed per invocation.
- **`graph.py`** — new `select_tables` node inserted between `route_databases` and
  `load_schema`; writes `state["selected_tables"]` (dict: `database_id -> [table names]`).
  When there is no catalog, or a DB has ≤ K tables, it selects **all** tables (so the bank
  and murder-mystery examples are unchanged).
- **`config.py`** — add `max_selected_tables: int | None = None` (unset = today's behavior).
- **`state.py`** — add `selected_tables` to `AgentState`.
- **`database.py` / `catalog.py`** — `schema_context` and `catalog_schema_context` accept
  an optional per-table allow-list so only the selected tables enter the schema blob.
- **Security note:** table-selection is **prompt-narrowing only, not a security
  boundary**. The read-only Postgres role, the read-only SQL guard, and
  `auto_block_sensitive_columns` remain the security layer. If the model ever names a
  table we didn't show it, the guard still governs execution.

### 4. Semantic annotation layer (descriptions + enum decoding)

Gives the model meaning, not just structure. Two data additions, three producers, and a
clear precedence.

**Catalog model additions (`catalog.py`):**
- `TableProfile.description: str | None`
- `ColumnProfile.description: str | None`
- `ColumnProfile.enum_values: dict[str, str] | None` (code → label, e.g.
  `{"100": "Submitted", "300": "Active", "600": "Closed"}`)
- `catalog_router_text` and `catalog_schema_context` render descriptions + enum legends
  when present.

**Producers and precedence (highest wins):**
1. **Hand-editable overlay** — `config/fineract_annotations.yaml` (`tables: <name>:
   description / columns: <col>: <text>`). Applied at **catalog load time**, so editing it
   + restarting the UI takes effect with **no re-index**. This is the user-customization
   surface; its text always overrides the AI text.
2. **AI auto-annotation** — new `src/db_agentic_system/annotate.py`: `annotate_catalog`
   sends each table (name, columns, FKs, sample rows) to the LLM in batches and fills empty
   `description` fields. Run **opt-in** via `db-agent index ... --annotate` and baked into
   the catalog JSON. Plain `index` stays fast and LLM-free.
3. **Deterministic enum enrichment** — new `src/db_agentic_system/enum_enrichment.py`
   (Fineract-aware): at index time, read `r_enum_value` (primary) — mapping `enum_name` to
   matching column names — and fill `ColumnProfile.enum_values`. Runs automatically **only
   when those tables exist**, so it's a no-op for the SQLite examples. `m_code_value`
   support is a documented extension, not v1.

**Wiring:** `config.py` gains `annotations_path: str | None`; catalog loading
(`graph.build_graph`, `web._load_optional_catalog`) applies the overlay via a
`merge_annotations(catalog, load_annotations(path))` helper in `catalog.py`. `cli.py`
`index` gains `--annotate` and invokes enum enrichment when applicable.

**Consumption:** the same descriptions/enum legends flow into (a) the per-table text for
table-selection ranking (§3) and (b) the schema context blob for SQL generation — so the
model both *finds* and *correctly queries* enum-coded tables.

### 5. Wiring & dependencies

- `pyproject.toml`: add `psycopg[binary]` to runtime deps; add a `[project.optional-
  dependencies].seed` extra with `requests` (used only by the seed script).
- `.env.example`: add
  `DATABASE_URL_FINERACT=postgresql+psycopg://agent_ro:<pw>@localhost:5432/fineract_default`.
- `src/db_agentic_system/static/app.js`: add a
  `"Fineract Core Banking · Learned catalog"` entry to `DATA_PROFILES`
  (`configPath: config/fineract.example.yaml`, `catalogPath: config/fineract_catalog.json`,
  `schemaSource: "learned"`). Bank stays default.
- `README.md`: document the new example and the runbook.

### 6. Tests

- `tests/test_table_selection.py` — with an **injected fake embedder** (deterministic):
  top-K correctness, lexical fallback, FK-neighbor expansion, and small-DB passthrough
  (≤ K tables → all selected).
- `tests/test_annotations.py` — overlay `merge_annotations` overrides AI/base descriptions
  (user-wins precedence); enum enrichment fills `enum_values` from fake `r_enum_value`
  rows and is a no-op when the table is absent; `annotate_catalog` fills empty descriptions
  using a fake LLM.
- A config parse test for `max_selected_tables`, `annotations_path`, and empty
  `include_tables`.
- Matches the existing `tests/` style (fakes injected, no network).

## Backward compatibility

- `max_selected_tables` unset ⇒ existing examples behave exactly as today.
- New config/catalog files and a new `DATA_PROFILES` entry are additive.
- Postgres dialect requires no guard changes (sqlglot already handles it).
- Annotation fields are optional; `annotations_path` unset and no `--annotate` ⇒ catalogs
  are structure-only, exactly as today. Enum enrichment is a no-op when `r_enum_value` is
  absent, so the SQLite examples are unaffected.

## Runbook

```
docker compose -f fineract/docker-compose.yml up -d
# wait for Fineract to finish booting (health check / log line)
pip install -e ".[seed]"
python fineract/seed_fineract.py
db-agent index --config config/fineract.example.yaml --output config/fineract_catalog.json --annotate
# restart the UI, then pick "Fineract Core Banking · Learned catalog"

# To customize descriptions later: edit config/fineract_annotations.yaml, restart the UI.
# No re-index needed — the overlay is applied at catalog load time.
```

## Scope boundaries (YAGNI)

- Single tenant (`default`), individual clients only (no group loans).
- No accounting/GL tables emphasized in seed data (schema is still fully exposed; seeding
  focuses on the banking domain — clients/loans/savings/transactions).
- Table-selection targets **learned-catalog** mode (Fineract ships a catalog). Runtime
  (live-introspection) mode falls back to selecting all tables.
- Persisting per-table embeddings into the catalog file is a possible future optimization;
  v1 uses the in-process cache.
- Enum enrichment covers `r_enum_value` in v1; `m_code_value` (configurable code sets) is a
  documented extension.
- AI annotation is opt-in (`--annotate`); the shipped `fineract_annotations.yaml` hand-
  covers the ~20–30 core banking tables so the example is good even without running the AI
  pass. Making the global SQL-gen prompt template user-configurable is out of scope (the
  win here is per-table context, not a rewritten global prompt).

## Risks / open items resolved at build time

- Exact Fineract image tag and Postgres env var names come from the official compose file
  (fetched during implementation), not from memory.
- Loan/savings product creation requires many required fields; the seed script encapsulates
  them.
- Fineract boot time (1–3 min) — the seed script polls readiness before seeding.

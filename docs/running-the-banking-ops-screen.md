# Running the banking operations screen locally

The "banking operations screen" is the agent's web UI (`db-agent-ui`) driven by one of
the two banking config profiles: **Bank Config** (`config/bank.example.yaml`, a local
SQLite retail-bank database) or **Fineract Core Banking**
(`config/fineract.example.yaml`, a live Apache Fineract Postgres schema).

Two things to know before you start, because the naming is misleading:

- **The screen is served at `/`, not `/ops`.** `GET /ops` returns 404. The only routes
  are `/`, `/static/*` and the `/api/*` endpoints (`src/db_agentic_system/web.py`).
- **`8123` is not the default port.** `db-agent-ui` binds `127.0.0.1:8000` unless
  `DB_AGENT_UI_PORT` says otherwise (`web.py:main`). Port 8123 comes from
  `.claude/launch.json`, which sets `DB_AGENT_UI_PORT=8123` for the `db-agent-ui`
  launch configuration. Steps below use 8123 to match it.

---

## 1. Dependencies

Python 3.11 or newer (`pyproject.toml` sets `requires-python = ">=3.11"`).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

That installs FastAPI, uvicorn, LangGraph/LangChain, SQLAlchemy, `psycopg[binary]`,
sqlglot and both provider bindings, and puts two commands on the path:
`db-agent` (CLI) and `db-agent-ui` (the screen).

Two optional extras, neither needed to serve the screen:

```bash
pip install -e ".[dev]"     # pytest, ruff
pip install -e ".[seed]"    # requests — only for fineract/seed_fineract.py
```

## 2. Environment variables

Copy the template and edit it:

```bash
cp .env.example .env
```

`.env` is loaded on import (`config.py` calls `load_dotenv()`), so it supplies both the
model credentials and the server settings — you do not need to export anything.

| Variable | Required? | Notes |
| --- | --- | --- |
| `DB_AGENT_PROVIDER` | no | `gemini` (default) or `openai`. |
| `GOOGLE_API_KEY` *or* `GEMINI_API_KEY` | yes, for Gemini | Either name works. |
| `OPENAI_API_KEY` *or* `OPENAI_ADMIN_KEY` | yes, for OpenAI | |
| `DB_AGENT_MODEL` | no | Defaults to `gemini-3.1-flash-lite`. |
| `DB_AGENT_EMBEDDING_MODEL` | no | Used for routing/table-selection embeddings. |
| `DB_AGENT_UI_HOST` | no | Defaults to `127.0.0.1`. |
| `DB_AGENT_UI_PORT` | no | Defaults to `8000`; set `8123` to match `.claude/launch.json`. |
| `DB_AGENT_SESSIONS_DB` | no | Defaults to `db/agent_sessions.db`. |
| `DATABASE_URL_BANK` | only for the Bank profiles | `config/bank.example.yaml` reads it via `uri_env`. |
| `DATABASE_URL_FINERACT` | only for the Fineract profile | `config/fineract.example.yaml` reads it via `uri_env`. |

**The API key is not optional in practice.** Without one, the screen still loads and the
sidebar still renders, but `/api/models` returns `{"models":[],"default_id":null}` — the
AI Model dropdown is empty — and every question fails with
`Missing Gemini credentials. Set GOOGLE_API_KEY or GEMINI_API_KEY…`.

## 3. Database setup

### The chat-session store: nothing to do

`db/agent_sessions.db` (the "Recent Chats" list) is a SQLite file that `SessionStore`
creates, along with its parent directory and table, the first time the screen handles a
chat. `db/agent_memory.sql` is a reference schema for a future persistent store — it is
not executed by anything and you do not need to run it.

### The banking data: this is the part that needs work

**Bank Config** (`config/bank.example.yaml`) reads its database URL from the
environment, so point `DATABASE_URL_BANK` at your own copy of the retail-bank SQLite
file:

```bash
DATABASE_URL_BANK=sqlite:////absolute/path/to/bank_sqlite.db
```

Four slashes after `sqlite:` for an absolute path, three for a path relative to the
working directory. Leave it unset and indexing stops with
`environment variable 'DATABASE_URL_BANK' is not set`.

If you would rather add a config of your own than reuse this one, note that the profile
dropdown is a hardcoded list in `src/db_agentic_system/static/app.js` (`DATA_PROFILES`) —
a new YAML file will not appear in the UI without an entry there.

**Then build the catalog**, because both bank profiles need one and it is not in the
repo — `config/*_catalog.json` is gitignored:

```bash
db-agent index --config config/bank.example.yaml --output config/bank_catalog.json
```

Indexing needs no API key unless you add `--annotate`. You can do the same thing from the
screen with the **Index Catalog** button.

Skipping this step is the most common failure. The sidebar flags it: an unindexed profile
reads "(catalog missing)" in the dropdown and `catalog missing — press Index Catalog` in
the line under it. The state is read from the server on every profile switch, so it
reflects the files actually on disk. Asking anyway returns:

```json
{"detail":"Catalog not found: config/bank_catalog.json. Learn it first with
`db-agent index --config <config> --output config/bank_catalog.json`, or press
Index Catalog in the web UI."}
```

This applies to the **Live schema** profile too, not just **Learned catalog**:
`bank.example.yaml` declares `catalog_path`, and `build_graph` requires that file
whenever no catalog was passed in (`graph.py:36`). Falling back to live schema there
would skip table selection and push every table into the prompt, which is why it stops
instead.

**Fineract Core Banking** needs the Docker stack instead — Postgres on host port 5433,
a seeded dataset, and the read-only `agent_ro` role. That runbook is
[`fineract/README.md`](../fineract/README.md); follow it, set `DATABASE_URL_FINERACT` in
`.env`, and index to `config/fineract_catalog.json`.

## 4. Serve it at 127.0.0.1:8123

```bash
source .venv/bin/activate
DB_AGENT_UI_PORT=8123 db-agent-ui
```

Or put `DB_AGENT_UI_PORT=8123` in `.env` and just run `db-agent-ui`. Either way uvicorn
reports:

```text
INFO:     Uvicorn running on http://127.0.0.1:8123 (Press CTRL+C to quit)
```

Open **<http://127.0.0.1:8123>** — the root path, not `/ops`.

In the sidebar: pick a **Config Profile** (`Bank Config · Learned catalog` for SQLite,
`Fineract Core Banking · Learned catalog` for the Docker stack), optionally pin a
**Database** instead of auto-routing, pick an **AI Model**, and ask a question. The right
panel streams the trace: standalone question, selected database, generated SQL,
validation errors and a result preview.

## 5. Check it without opening a browser

```bash
curl -s http://127.0.0.1:8123/api/status
```

```json
{"provider":"gemini","model":"gemini-3.1-flash-lite","model_configured":true,
 "default_config_exists":true,"default_catalog_exists":true,"session_count":0}
```

`model_configured: false` means the API key is missing; `default_catalog_exists: false`
means you have not indexed yet. To confirm a profile resolves and its catalog is loaded —
`tables` is `null` until you index, and a number afterwards:

```bash
curl -s -X POST http://127.0.0.1:8123/api/databases \
  -H 'Content-Type: application/json' \
  -d '{"config_path":"config/bank.example.yaml","catalog_path":"config/bank_catalog.json"}'
```

And a full grounded query, which does need the API key:

```bash
curl -s -X POST http://127.0.0.1:8123/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"How many accounts are there?",
       "config_path":"config/bank.example.yaml",
       "catalog_path":"config/bank_catalog.json",
       "schema_source":"learned"}'
```

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `404` at `/ops` | The screen is served at `/` | Open `http://127.0.0.1:8123/` |
| Server comes up on 8000 | `DB_AGENT_UI_PORT` unset | Set it to `8123` in the shell or `.env` |
| `Catalog not found: config/bank_catalog.json` | Catalog never built | `db-agent index …`, or the **Index Catalog** button |
| `environment variable 'DATABASE_URL_BANK' is not set` | Bank profile without its URL | Set it in `.env` to your local SQLite path |
| `Missing Gemini credentials` | No API key | Set `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) in `.env` |
| AI Model dropdown empty | Same — `/api/models` only lists providers with a key | Same |
| `environment variable 'DATABASE_URL_FINERACT' is not set` | Fineract profile without its URL | Set it in `.env`; see `fineract/README.md` |
| Rate-limit / `RESOURCE_EXHAUSTED` after retries | Free-tier model quota | Wait, or switch provider in the AI Model dropdown |

# Fineract example stack

Local, testing-only Apache Fineract + PostgreSQL that backs the agent's
**Fineract Core Banking** example. The agent queries Fineract's `fineract_default`
Postgres database **read-only** (as the `agent_ro` role), exposing the full
~300-table core-banking schema.

> FOR LOCAL TESTING ONLY — not a production configuration.

## 1. Bring it up

```bash
docker compose -f fineract/docker-compose.yml up -d
```

Fineract boots + runs its Liquibase schema migration on first start (1–3 min).
Wait for it to report healthy:

```bash
curl -sk https://localhost:8443/fineract-provider/actuator/health
# {"status":"UP"}
```

- API base: `https://localhost:8443/fineract-provider/api/v1` (self-signed TLS — use `curl -k`)
- Admin: `mifos` / `password`; tenant header: `Fineract-Platform-TenantId: default`
- Postgres: `localhost:5433` (compose maps `5433->5432` to avoid a native Postgres on 5432),
  superuser `postgres` / `fineract_pg_pw`, DB `fineract_default`

## 2. Seed realistic data

```bash
pip install -e ".[seed]"        # installs requests
python fineract/seed_fineract.py
```

Creates an office, a loan officer, several clients, one loan product + one savings
product, then active loans and savings accounts with repayments/deposits.

## 3. Create the read-only role for the agent (run once, after boot)

```bash
docker compose -f fineract/docker-compose.yml exec -T db \
  psql -U postgres -f - < fineract/create-readonly-role.sql
```

The agent connects as `agent_ro` (SELECT-only) via
`DATABASE_URL_FINERACT=postgresql+psycopg://agent_ro:agent_ro_pw@localhost:5433/fineract_default`
(see `.env.example`).

## 4. Index the catalog (with AI annotations)

```bash
db-agent index --config config/fineract.example.yaml \
  --output config/fineract_catalog.json --annotate
```

`--annotate` runs an LLM pass to fill table/column descriptions (needs a model
provider in `.env`); drop it to index structure only. Enum-coded columns are
decoded deterministically from Fineract's own `r_enum_value` regardless.

To customize descriptions later, edit `config/fineract_annotations.yaml` and
restart the UI — the overlay is applied at catalog load, so **no re-index** is
needed.

## 5. Use it

Start the UI (`db-agent-ui`) and pick **"Fineract Core Banking · Learned catalog"**.

## Tear down (removes all data)

```bash
docker compose -f fineract/docker-compose.yml down -v
```

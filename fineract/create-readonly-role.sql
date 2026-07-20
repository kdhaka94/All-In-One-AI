-- Run once, AFTER Fineract has migrated the schema, against the postgres superuser.
-- Creates a SELECT-only role the agent uses to query fineract_default.
DROP ROLE IF EXISTS agent_ro;
CREATE ROLE agent_ro LOGIN PASSWORD 'agent_ro_pw';
GRANT CONNECT ON DATABASE fineract_default TO agent_ro;
\connect fineract_default
GRANT USAGE ON SCHEMA public TO agent_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agent_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO agent_ro;

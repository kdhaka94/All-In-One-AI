-- Runs once at first Postgres init (as POSTGRES_USER). Fineract migrates the
-- schema into fineract_default on boot; both databases must exist first.
CREATE DATABASE fineract_tenants;
CREATE DATABASE fineract_default;

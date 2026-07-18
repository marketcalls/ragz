-- LiteLLM's own model store lives in a separate database on the shared Postgres.
-- Its content is disposable: RagHub replays the full model config on every change
-- and on startup, so wiping this DB only requires one replay to heal.
--
-- NOTE: docker-entrypoint-initdb.d scripts run ONLY when the pgdata volume is
-- first initialized. If you are upgrading an EXISTING deployment (volume already
-- present), create the database once by hand:
--   docker exec raghub-postgres-1 psql -U raghub -d raghub -c "CREATE DATABASE litellm;"
CREATE DATABASE litellm;

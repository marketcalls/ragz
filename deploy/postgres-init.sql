-- LiteLLM's own model store lives in a separate database on the shared Postgres.
-- Its content is disposable: RagHub replays the full model config on every change
-- and on startup, so wiping this DB only requires one replay to heal.
CREATE DATABASE litellm;

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Deployment Tracker"
    # Postgres by default now, matching production and avoiding SQLite/Postgres dialect
    # drift in migrations. Run `docker compose up -d db` for a local instance matching
    # these credentials (docker-compose.yml).
    database_url: str = "postgresql+psycopg2://deploy_tracker:changeme@localhost:5432/deploy_tracker"

    # In-house task API (Phase 3): POST {base_url}/login with
    # {username, password, ignorePermissions} returns a bearer token.
    task_api_base_url: str | None = None
    task_api_username: str | None = None
    task_api_password: str | None = None
    # The API's /login accepts an ignorePermissions flag for service-style logins — it comes
    # back with an empty `permissions` list on the user object when set. Kept configurable
    # (not hardcoded true) since bypassing permission scoping is worth confirming with
    # whoever owns the CRM API rather than assuming it's fine long-term.
    task_api_ignore_permissions: bool = True

    # Daily User/Client sync job (Phase 3) — hour of day, 24h clock, local time
    task_api_sync_hour: int = 6

    # /get-orders (deployable-tasks import) is scoped to this team's hall + machine group
    # (confirmed by the user: hall 5 / machine group 13 = "Team Rajib") — configurable
    # rather than hardcoded since this is specific to this DevOps team's CRM setup, not
    # a fixed value of the API itself.
    task_api_deployable_hall_id: int = 5
    task_api_deployable_machine_group_id: int = 13

    # Signs the login session cookie (Starlette SessionMiddleware). The default below is
    # fine for local dev only — anyone who reads it could forge a session cookie, so every
    # real deployment must override this in its own .env with a long random value (e.g.
    # `python -c "import secrets; print(secrets.token_hex(32))"`).
    session_secret_key: str = "dev-only-insecure-secret-change-me"


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    database_url: str
    ollama_base_url: str
    ollama_model: str
    ollama_embed_model: str
    jwt_secret: str
    # Base64-encoded symmetric key (>=32 raw bytes) used to derive the
    # at-rest PII encryption subkeys in `app.security.crypto` (Task 23).
    # Never hardcoded -- generate one with, e.g.,
    # `python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"`.
    encryption_key: str
    session_timeout_minutes: int
    escalation_confidence_threshold: float = 0.6
    # OpenRouteService API key for the route-planning feature (directions +
    # geocoding, app/integrations/openrouteservice.py). Defaults to "" (not
    # a required field) so importing app.config never breaks for code paths
    # that don't touch route-planning -- every existing test in this suite
    # imports app.config transitively and doesn't set this. A caller that
    # actually needs it (openrouteservice.py) is responsible for treating an
    # empty value as "not configured".
    ors_api_key: str = ""
    # Opt-in chat escalation email (app/integrations/email.py; see
    # docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md).
    # Every field is optional with an empty/safe default, for the same reason
    # as ors_api_key above: importing app.config must never break for code
    # paths (and tests) that don't send email. An empty smtp_host means "not
    # configured" -- send_escalation_email raises immediately instead of
    # attempting a connection, and POST /chat/messages/{id}/escalate reports
    # email_sent=false while still creating the ticket. smtp_port 587 +
    # smtp_use_tls=True is the standard STARTTLS submission setup.
    # smtp_from_address falls back to smtp_username when empty. smtp_password
    # is only ever passed to smtplib's login() -- never logged.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_address: str = ""
    smtp_use_tls: bool = True
    escalation_email_to: str = "CIHE241731@student.edu.cihe.au"
    # Comma-separated list of origins the frontend is served from, allowed to
    # make cross-origin requests to this API (see CORSMiddleware in main.py).
    # Defaults cover the two ways this POC actually runs the frontend: the
    # Vite dev server (`npm run dev`, port 5173) and the Docker Compose
    # `frontend` service (see docker-compose.yml, which maps 3000:3000).
    cors_allowed_origins: str = "http://localhost:5173,http://localhost:3000"

    model_config = SettingsConfigDict(env_file=".env")

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        """`cors_allowed_origins` split on commas, trimmed, empties dropped."""
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


settings = Settings()

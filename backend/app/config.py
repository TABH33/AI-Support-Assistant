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

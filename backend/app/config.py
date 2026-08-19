"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    database_url: str
    ollama_base_url: str
    ollama_model: str
    ollama_embed_model: str
    jwt_secret: str
    session_timeout_minutes: int
    escalation_confidence_threshold: float = 0.6
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

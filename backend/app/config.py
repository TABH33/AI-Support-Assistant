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

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()

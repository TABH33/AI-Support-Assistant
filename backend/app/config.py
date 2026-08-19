"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    database_url: str
    ollama_base_url: str
    ollama_model: str
    ollama_embed_model: str
    jwt_secret: str
    session_timeout_minutes: int

    class Config:
        env_file = ".env"


settings = Settings()

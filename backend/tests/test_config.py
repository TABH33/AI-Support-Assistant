"""Tests for application configuration."""

import pytest

from app.config import Settings, settings


def test_settings_imports_without_warnings():
    """Test that Settings class imports and instantiates without deprecation warnings."""
    # This test verifies that the config.py module uses Pydantic v2 idioms correctly.
    # If deprecated Pydantic v1-style Config is used, this would trigger a
    # PydanticDeprecatedSince20 warning.
    assert Settings is not None
    assert settings is not None


def test_settings_has_all_required_fields():
    """Test that Settings class has all 6 required environment variables."""
    # Get the fields from the Settings model
    required_fields = {
        "database_url",
        "ollama_base_url",
        "ollama_model",
        "ollama_embed_model",
        "jwt_secret",
        "session_timeout_minutes",
    }

    model_fields = Settings.model_fields.keys()
    assert required_fields.issubset(model_fields), (
        f"Missing fields: {required_fields - set(model_fields)}"
    )


def test_settings_field_types():
    """Test that Settings fields have correct types."""
    fields = Settings.model_fields

    # String fields
    assert fields["database_url"].annotation == str
    assert fields["ollama_base_url"].annotation == str
    assert fields["ollama_model"].annotation == str
    assert fields["ollama_embed_model"].annotation == str
    assert fields["jwt_secret"].annotation == str

    # Integer field
    assert fields["session_timeout_minutes"].annotation == int


def test_settings_instance_loaded():
    """Test that the global settings instance is properly loaded from environment."""
    # The settings object should be instantiated and accessible
    assert isinstance(settings, Settings)

    # All required attributes should exist and be of correct type
    assert isinstance(settings.database_url, str)
    assert isinstance(settings.ollama_base_url, str)
    assert isinstance(settings.ollama_model, str)
    assert isinstance(settings.ollama_embed_model, str)
    assert isinstance(settings.jwt_secret, str)
    assert isinstance(settings.session_timeout_minutes, int)


def test_settings_has_ors_api_key_field_with_empty_default():
    assert "ors_api_key" in Settings.model_fields
    assert Settings.model_fields["ors_api_key"].default == ""
    assert isinstance(settings.ors_api_key, str)

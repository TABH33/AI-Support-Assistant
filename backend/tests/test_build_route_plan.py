"""Tests for build_route_plan, build_route_summary_prompt, summarize_route_plan."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.route_planning import (
    ROUTE_DATA_UNAVAILABLE_TEXT,
    RoutePlanResult,
    Warning,
    build_route_plan,
    build_route_summary_prompt,
    summarize_route_plan,
)
from app.integrations.open_meteo import ForecastResult
from app.integrations.openrouteservice import (
    Coordinates,
    GeocodingError,
    RouteResult,
    RouteServiceRequestError,
)
from app.models import Base

_ORIGIN = Coordinates(latitude=-33.8688, longitude=151.2093)
_DESTINATION = Coordinates(latitude=-33.8150, longitude=151.0011)
_GEOMETRY = {
    "type": "LineString",
    "coordinates": [[151.2093, -33.8688], [151.15, -33.84], [151.0011, -33.8150]],
}


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def session(engine):
    with Session(engine) as db_session:
        yield db_session


def test_build_route_plan_returns_distance_duration_and_geometry(session):
    with (
        patch("app.ai.route_planning.get_directions") as mock_directions,
        patch("app.ai.route_planning.get_forecast") as mock_forecast,
    ):
        mock_directions.return_value = RouteResult(
            geometry=_GEOMETRY, distance_km=23.4, duration_min=38.2
        )
        mock_forecast.return_value = ForecastResult(
            precipitation_probability=10.0, wind_speed_kmh=10.0, visibility_m=20000.0
        )

        result = build_route_plan(_ORIGIN, _DESTINATION, db=session)

        assert result.unavailable is False
        assert result.distance_km == 23.4
        assert result.duration_min == 38.2
        assert result.geometry == _GEOMETRY


def test_build_route_plan_geocodes_string_origin_and_destination(session):
    with (
        patch("app.ai.route_planning.geocode") as mock_geocode,
        patch("app.ai.route_planning.get_directions") as mock_directions,
        patch("app.ai.route_planning.get_forecast") as mock_forecast,
    ):
        mock_geocode.side_effect = [_ORIGIN, _DESTINATION]
        mock_directions.return_value = RouteResult(
            geometry=_GEOMETRY, distance_km=23.4, duration_min=38.2
        )
        mock_forecast.return_value = ForecastResult(
            precipitation_probability=10.0, wind_speed_kmh=10.0, visibility_m=20000.0
        )

        result = build_route_plan("Sydney CBD", "Parramatta", db=session)

        assert result.unavailable is False
        assert mock_geocode.call_count == 2
        mock_directions.assert_called_once_with(_ORIGIN, _DESTINATION, None)


def test_build_route_plan_returns_unavailable_on_geocoding_failure(session):
    with patch("app.ai.route_planning.geocode") as mock_geocode:
        mock_geocode.side_effect = GeocodingError("no match")

        result = build_route_plan("Nowhere", "Parramatta", db=session)

        assert result.unavailable is True
        assert result.distance_km is None
        assert result.duration_min is None
        assert result.geometry is None
        assert result.warnings == []


def test_build_route_plan_returns_unavailable_on_directions_failure(session):
    with patch("app.ai.route_planning.get_directions") as mock_directions:
        mock_directions.side_effect = RouteServiceRequestError("ORS is down")

        result = build_route_plan(_ORIGIN, _DESTINATION, db=session)

        assert result.unavailable is True


def test_build_route_summary_prompt_includes_distance_duration_and_warnings():
    route_plan = RoutePlanResult(
        distance_km=23.4,
        duration_min=38.2,
        geometry=_GEOMETRY,
        warnings=[
            Warning(
                latitude=-33.84,
                longitude=151.15,
                distance_from_origin_km=12.0,
                type="risk_zone",
                severity="high",
                description="4 harsh-braking events recorded near this point.",
            )
        ],
    )

    messages = build_route_summary_prompt(route_plan, "Sydney CBD", "Parramatta")

    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "23.4" in messages[1]["content"]
    assert "38" in messages[1]["content"]
    assert "harsh-braking" in messages[1]["content"]


def test_build_route_summary_prompt_handles_no_warnings():
    route_plan = RoutePlanResult(distance_km=10.0, duration_min=15.0, geometry=_GEOMETRY, warnings=[])

    messages = build_route_summary_prompt(route_plan, "A", "B")

    assert "no warnings" in messages[1]["content"].lower()


def test_summarize_route_plan_calls_llm_with_built_prompt():
    route_plan = RoutePlanResult(distance_km=10.0, duration_min=15.0, geometry=_GEOMETRY, warnings=[])
    with patch(
        "app.ai.route_planning.chat_completion", return_value="Short route, no warnings."
    ) as mock_chat:
        summary = summarize_route_plan(route_plan, "A", "B")

    assert summary == "Short route, no warnings."
    mock_chat.assert_called_once()


def test_route_data_unavailable_text_is_a_nonempty_constant():
    assert isinstance(ROUTE_DATA_UNAVAILABLE_TEXT, str)
    assert len(ROUTE_DATA_UNAVAILABLE_TEXT) > 0

"""Tests for build_route_plan, build_route_summary_prompt, summarize_route_plan."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.route_planning import (
    GEOCODING_FAILED_TEXT,
    ROUTE_DATA_UNAVAILABLE_TEXT,
    UNAVAILABLE_REASON_GEOCODING,
    UNAVAILABLE_REASON_SERVICE,
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
    RouteServiceResponseError,
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


# ---------------------------------------------------------------------------
# Final-review Fix 5: an unresolvable place name is not a service outage.
# ---------------------------------------------------------------------------


def test_geocoding_failure_reports_a_place_specific_reason_and_message(session):
    with patch("app.ai.route_planning.geocode") as mock_geocode:
        mock_geocode.side_effect = GeocodingError("no match")

        result = build_route_plan("Parramattaa", "Sydney CBD", db=session)

    assert result.unavailable is True
    assert result.unavailable_reason == UNAVAILABLE_REASON_GEOCODING
    # Names the exact place that failed, so the user can fix the typo.
    assert "Parramattaa" in result.unavailable_message
    # ...and is NOT the "try again shortly" advice, which cannot help here.
    assert result.unavailable_message != ROUTE_DATA_UNAVAILABLE_TEXT


def test_geocoding_failure_names_the_destination_when_the_origin_resolved(session):
    with patch("app.ai.route_planning.geocode") as mock_geocode:
        mock_geocode.side_effect = [_ORIGIN, GeocodingError("no match")]

        result = build_route_plan("Sydney CBD", "Parramattaa", db=session)

    assert result.unavailable_reason == UNAVAILABLE_REASON_GEOCODING
    assert "Parramattaa" in result.unavailable_message
    assert "Sydney CBD" not in result.unavailable_message


@pytest.mark.parametrize(
    "error",
    [RouteServiceRequestError("ORS is down"), RouteServiceResponseError("garbage body")],
)
def test_service_failure_reports_the_generic_unavailable_reason(session, error):
    with patch("app.ai.route_planning.get_directions") as mock_directions:
        mock_directions.side_effect = error

        result = build_route_plan(_ORIGIN, _DESTINATION, db=session)

    assert result.unavailable is True
    assert result.unavailable_reason == UNAVAILABLE_REASON_SERVICE
    assert result.unavailable_message == ROUTE_DATA_UNAVAILABLE_TEXT


def test_successful_plan_carries_no_unavailable_reason_or_message(session):
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
    assert result.unavailable_reason is None
    assert result.unavailable_message is None


# ---------------------------------------------------------------------------
# Final-review Fix 6a: a DB failure during warning evaluation must degrade,
# not propagate as a 500.
# ---------------------------------------------------------------------------


def test_db_failure_during_risk_zone_evaluation_degrades_instead_of_raising(session):
    with (
        patch("app.ai.route_planning.get_directions") as mock_directions,
        patch("app.ai.route_planning.get_forecast") as mock_forecast,
        patch("app.ai.route_planning.evaluate_risk_zone_warnings") as mock_risk,
    ):
        mock_directions.return_value = RouteResult(
            geometry=_GEOMETRY, distance_km=23.4, duration_min=38.2
        )
        mock_forecast.return_value = ForecastResult(
            precipitation_probability=10.0, wind_speed_kmh=10.0, visibility_m=20000.0
        )
        mock_risk.side_effect = OperationalError("SELECT 1", {}, Exception("connection lost"))

        # Must not raise -- this used to propagate out of build_route_plan
        # and become a 500 from POST /route-plan and POST /chat.
        result = build_route_plan(_ORIGIN, _DESTINATION, db=session)

    assert result.unavailable is True
    assert result.unavailable_reason == UNAVAILABLE_REASON_SERVICE
    assert result.unavailable_message == ROUTE_DATA_UNAVAILABLE_TEXT
    assert result.warnings == []


def test_non_database_error_during_warning_evaluation_still_propagates(session):
    """The SQLAlchemyError guard is deliberately narrow: a genuine bug in
    this module's own logic must still surface loudly rather than being
    silently reported to the user as "route data unavailable"."""
    with (
        patch("app.ai.route_planning.get_directions") as mock_directions,
        patch("app.ai.route_planning.evaluate_weather_warnings") as mock_weather,
    ):
        mock_directions.return_value = RouteResult(
            geometry=_GEOMETRY, distance_km=23.4, duration_min=38.2
        )
        mock_weather.side_effect = TypeError("programming error")

        with pytest.raises(TypeError):
            build_route_plan(_ORIGIN, _DESTINATION, db=session)


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


def test_geocoding_failed_text_is_a_distinct_place_templated_constant():
    assert GEOCODING_FAILED_TEXT != ROUTE_DATA_UNAVAILABLE_TEXT
    assert "Nowhereville" in GEOCODING_FAILED_TEXT.format(place="Nowhereville")

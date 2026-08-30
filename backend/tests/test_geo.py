"""Tests for app.geo."""
import pytest

from app.geo import haversine_distance_km


def test_haversine_distance_km_zero_for_identical_points():
    assert haversine_distance_km(-33.8688, 151.2093, -33.8688, 151.2093) == pytest.approx(0.0, abs=1e-6)


def test_haversine_distance_km_known_distance():
    # Sydney CBD to Parramatta is roughly 20-24km as the crow flies.
    distance = haversine_distance_km(-33.8688, 151.2093, -33.8150, 151.0011)
    assert 15.0 < distance < 30.0


def test_haversine_distance_km_is_symmetric():
    a = haversine_distance_km(-33.8688, 151.2093, -33.8150, 151.0011)
    b = haversine_distance_km(-33.8150, 151.0011, -33.8688, 151.2093)
    assert a == pytest.approx(b)

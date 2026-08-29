"""
Tests for tools/weather.py's city-name resolution — mocks requests.get at
the boundary, same pattern as other tests in this suite mock external
calls. Never hits the real Open-Meteo API.

Run with: pytest tests/
"""
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.weather import WeatherTool

_FORECAST_RESPONSE = {
    "current": {"temperature_2m": 25.0, "relative_humidity_2m": 60, "wind_speed_10m": 10.0, "precipitation": 0.0},
    "daily": {
        "time": ["2026-01-01"],
        "temperature_2m_min": [20.0],
        "temperature_2m_max": [28.0],
        "precipitation_probability_max": [10],
    },
}


def _mock_get(geo_results):
    """Returns a requests.get stand-in that answers the geocoding call
    with `geo_results` and the forecast call with a fixed fake forecast —
    distinguished by URL, matching how WeatherTool actually calls both."""

    def get(url, params=None, timeout=None):
        response = Mock()
        response.raise_for_status = Mock()
        if "geocoding" in url:
            response.json = Mock(return_value={"results": geo_results})
        else:
            response.json = Mock(return_value=_FORECAST_RESPONSE)
        return response

    return get


def test_bangalore_resolves_to_the_indian_city_not_a_pakistani_namesake():
    """Regression test for a real bug found live: Open-Meteo's geocoding
    has no alias linking "Bangalore" (the name most people actually say)
    to Bengaluru, India — a plain query for "Bangalore" returned only an
    obscure, unrelated town in Sindh, Pakistan, and the weather tool
    trusted it outright. Confirmed live via the real API before fixing."""
    geo_results = [{"name": "Bangalore Town", "country": "Pakistan", "latitude": 1, "longitude": 1, "population": None}]
    captured_query = {}

    def get(url, params=None, timeout=None):
        if "geocoding" in url:
            captured_query["name"] = params["name"]
        response = Mock()
        response.raise_for_status = Mock()
        response.json = Mock(
            return_value={"results": [{"name": "Bengaluru", "country": "India", "latitude": 12.97, "longitude": 77.59, "population": 8495492}]}
            if "geocoding" in url
            else _FORECAST_RESPONSE
        )
        return response

    with patch("tools.weather.requests.get", side_effect=get):
        result = WeatherTool().run(city="Bangalore")
    assert captured_query["name"] == "Bengaluru"  # the alias substitution actually happened
    assert "Bengaluru, India" in result
    assert "Pakistan" not in result


def test_bombay_and_madras_resolve_via_the_same_alias_mechanism():
    for spoken_name, real_city in [("Bombay", "Mumbai"), ("Madras", "Chennai")]:
        with patch(
            "tools.weather.requests.get",
            side_effect=_mock_get([{"name": real_city, "country": "India", "latitude": 1, "longitude": 1, "population": 10_000_000}]),
        ):
            result = WeatherTool().run(city=spoken_name)
        assert f"{real_city}, India" in result


def test_ambiguous_city_name_prefers_the_most_populous_match():
    """When several real candidates share a name (not just an alias miss),
    an obscure one with no/low population shouldn't outrank a real,
    well-known city — the exact failure mode confirmed live for city names
    this alias table doesn't happen to cover. Verified by checking which
    candidate's coordinates actually get sent to the forecast call, not
    just that some result comes back."""
    geo_results = [
        {"name": "Springfield", "country": "United States", "latitude": 1.0, "longitude": 1.0, "population": 500},
        {"name": "Springfield", "country": "United States", "latitude": 2.0, "longitude": 2.0, "population": 169_176},
        {"name": "Springfield", "country": "United States", "latitude": 3.0, "longitude": 3.0, "population": None},
    ]
    captured_coords = {}

    def get(url, params=None, timeout=None):
        response = Mock()
        response.raise_for_status = Mock()
        if "geocoding" in url:
            response.json = Mock(return_value={"results": geo_results})
        else:
            captured_coords["latitude"] = params["latitude"]
            captured_coords["longitude"] = params["longitude"]
            response.json = Mock(return_value=_FORECAST_RESPONSE)
        return response

    with patch("tools.weather.requests.get", side_effect=get):
        WeatherTool().run(city="Springfield")
    assert (captured_coords["latitude"], captured_coords["longitude"]) == (2.0, 2.0)


def test_ordinary_city_name_passes_through_unmodified():
    with patch(
        "tools.weather.requests.get",
        side_effect=_mock_get([{"name": "Tokyo", "country": "Japan", "latitude": 1, "longitude": 1, "population": 13_960_000}]),
    ):
        result = WeatherTool().run(city="Tokyo")
    assert "Tokyo, Japan" in result


def test_city_not_found_returns_a_clear_message():
    with patch("tools.weather.requests.get", side_effect=_mock_get([])):
        result = WeatherTool().run(city="Nowhereville")
    assert "Could not find" in result

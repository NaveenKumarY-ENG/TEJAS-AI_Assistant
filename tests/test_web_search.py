"""
Tests for tools/web_search.py. No real network calls — Tavily's API is
mocked. Run with: pytest tests/
"""
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.web_search import WebSearchTool, _looks_unconfigured

tool = WebSearchTool()


def test_looks_unconfigured_detects_empty_key():
    assert _looks_unconfigured("") is True


def test_looks_unconfigured_detects_placeholder_markers():
    assert _looks_unconfigured("tvly-your-key-here") is True
    assert _looks_unconfigured("REPLACE_ME") is True
    assert _looks_unconfigured("<your-tavily-key>") is True


def test_looks_unconfigured_accepts_a_real_looking_key():
    assert _looks_unconfigured("tvly-abc123XYZ789randomtoken") is False


def test_run_reports_not_configured_for_placeholder_key():
    with patch("tools.web_search.config.search_api_key", "tvly-your-key-here"):
        result = tool.run(query="anything")
    assert "not configured" in result.lower()
    assert "tavily.com" in result


def test_run_returns_quick_answer_and_results_on_success():
    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    fake_response.json.return_value = {
        "answer": "It's currently 24°C in Bengaluru.",
        "results": [
            {"title": "Bengaluru Weather", "content": "Sunny with light clouds" * 10, "url": "https://example.com/weather"},
        ],
    }
    with patch("tools.web_search.config.search_api_key", "tvly-realkey123"), patch(
        "tools.web_search.requests.post", return_value=fake_response
    ):
        result = tool.run(query="weather in Bengaluru")
    assert "Quick answer: It's currently 24°C in Bengaluru." in result
    assert "Bengaluru Weather" in result
    assert "https://example.com/weather" in result


def test_run_handles_no_results_gracefully():
    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    fake_response.json.return_value = {"results": []}
    with patch("tools.web_search.config.search_api_key", "tvly-realkey123"), patch(
        "tools.web_search.requests.post", return_value=fake_response
    ):
        result = tool.run(query="something with truly no results")
    assert result == "No results found."


def test_run_surfaces_request_errors_instead_of_raising():
    with patch("tools.web_search.config.search_api_key", "tvly-realkey123"), patch(
        "tools.web_search.requests.post", side_effect=requests.ConnectionError("network unreachable")
    ):
        result = tool.run(query="anything")
    assert "Error during web search" in result


def test_run_surfaces_http_error_status_instead_of_raising():
    fake_response = Mock()
    fake_response.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")
    with patch("tools.web_search.config.search_api_key", "tvly-realkey123"), patch(
        "tools.web_search.requests.post", return_value=fake_response
    ):
        result = tool.run(query="anything")
    assert "Error during web search" in result


def test_results_are_capped_at_five():
    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    fake_response.json.return_value = {
        "results": [{"title": f"Result {i}", "content": "text", "url": f"https://example.com/{i}"} for i in range(10)]
    }
    with patch("tools.web_search.config.search_api_key", "tvly-realkey123"), patch(
        "tools.web_search.requests.post", return_value=fake_response
    ):
        result = tool.run(query="anything")
    assert result.count("Result ") == 5

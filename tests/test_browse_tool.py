"""
Tests for tools/browse_tool.py (open_website) — the direct fix for "opening
flipkart.com (or any site other than amazon.in) does nothing", since
nothing else in this codebase ever navigates a browser anywhere but
amazon.in. No real browser/network involved — a tiny fake Playwright-shaped
Page/Context stand-in, same style as tests/test_shopping_tool.py.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import browse_tool
from tools.browse_tool import InvalidWebsite, OpenWebsiteTool, resolve_url


class FakePage:
    def __init__(self):
        self.url = None

    def goto(self, url, wait_until=None):
        self.url = url

    def bring_to_front(self):
        pass


class FakeContext:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page


# --- resolve_url ---


def test_resolve_url_maps_known_alias():
    assert resolve_url("flipkart") == "https://www.flipkart.com"


def test_resolve_url_is_case_and_whitespace_insensitive_for_aliases():
    assert resolve_url("  Flipkart  ") == "https://www.flipkart.com"


def test_resolve_url_handles_multi_word_alias():
    assert resolve_url("google maps") == "https://maps.google.com"


def test_resolve_url_passes_through_a_full_url_unchanged():
    assert resolve_url("https://example.com/path?q=1") == "https://example.com/path?q=1"


def test_resolve_url_accepts_a_bare_domain_without_alias():
    assert resolve_url("flipkart.com") == "https://flipkart.com"
    assert resolve_url("news.ycombinator.com") == "https://news.ycombinator.com"


def test_resolve_url_best_effort_dot_coms_a_bare_unknown_word():
    assert resolve_url("cnn") == "https://cnn.com"


def test_resolve_url_rejects_empty_input():
    with pytest.raises(InvalidWebsite):
        resolve_url("")
    with pytest.raises(InvalidWebsite):
        resolve_url("   ")


def test_resolve_url_rejects_disallowed_schemes():
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,<script>1</script>"):
        with pytest.raises(InvalidWebsite):
            resolve_url(bad)


def test_resolve_url_accepts_http_scheme_too():
    assert resolve_url("http://example.com") == "http://example.com"


# --- OpenWebsiteTool.run ---


def test_run_returns_disabled_message_when_live_ai_disabled():
    with patch.object(browse_tool.config, "live_ai_enabled", False):
        result = OpenWebsiteTool().run(site="flipkart")
    assert "disabled" in result.lower()


def test_run_returns_setup_message_when_browser_unavailable():
    with patch.object(browse_tool.config, "live_ai_enabled", True), patch.object(
        browse_tool.browser, "available", return_value=False
    ):
        result = OpenWebsiteTool().run(site="flipkart")
    assert "isn't set up" in result


def test_run_reports_invalid_site_without_navigating():
    with patch.object(browse_tool.config, "live_ai_enabled", True), patch.object(
        browse_tool.browser, "available", return_value=True
    ), patch.object(browse_tool.browser, "get_context") as mock_get_context:
        result = OpenWebsiteTool().run(site="javascript:alert(1)")
    assert "isn't a website" in result
    mock_get_context.assert_not_called()


def test_run_navigates_to_the_resolved_url_and_confirms():
    page = FakePage()
    with patch.object(browse_tool.config, "live_ai_enabled", True), patch.object(
        browse_tool.browser, "available", return_value=True
    ), patch.object(browse_tool.browser, "get_context", return_value=FakeContext(page)):
        result = OpenWebsiteTool().run(site="flipkart")
    assert page.url == "https://www.flipkart.com"
    assert "https://www.flipkart.com" in result
    assert "Opened" in result


def test_run_surfaces_navigation_errors_instead_of_raising():
    class BrokenPage(FakePage):
        def goto(self, url, wait_until=None):
            raise RuntimeError("net::ERR_CONNECTION_REFUSED")

    with patch.object(browse_tool.config, "live_ai_enabled", True), patch.object(
        browse_tool.browser, "available", return_value=True
    ), patch.object(browse_tool.browser, "get_context", return_value=FakeContext(BrokenPage())):
        result = OpenWebsiteTool().run(site="example.com")
    assert "Something went wrong" in result

"""
Tests for tools/flipkart_tool.py. No real browser/Flipkart involved — a
tiny fake Playwright-shaped Page/anchor stand-in exercises the extraction
logic directly, same style as tests/test_shopping_tool.py's Amazon tests.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import flipkart_tool
from tools.flipkart_tool import ShopFlipkartTool


class FakeAnchor:
    def __init__(self, href, title=None, text="", container_text=""):
        self._href = href
        self._title = title
        self._text = text
        self._container_text = container_text

    def get_attribute(self, name):
        if name == "href":
            return self._href
        if name == "title":
            return self._title
        return None

    def inner_text(self):
        return self._text

    def evaluate(self, script):
        return self._container_text


def make_anchor(href="/some-phone/p/itm123", title="Phone X", container_text="₹19,999\n4.3 out of 5 ★"):
    return FakeAnchor(href=href, title=title, container_text=container_text)


class FakePage:
    def __init__(self, anchors):
        self._anchors = anchors
        self.url = None

    def query_selector_all(self, selector):
        assert selector == 'a[href*="/p/"]'
        return self._anchors

    def goto(self, url, wait_until=None):
        self.url = url

    def bring_to_front(self):
        pass

    def wait_for_selector(self, selector, timeout=None):
        if not self._anchors:
            raise TimeoutError("no results")


class FakeContext:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page


# --- _extract_results ---


def test_extract_results_parses_title_price_rating_link():
    results = flipkart_tool._extract_results(FakePage([make_anchor()]))
    assert results == [
        {
            "title": "Phone X",
            "price": "₹19,999",
            "rating": "4.3",
            "link": "https://www.flipkart.com/some-phone/p/itm123",
        }
    ]


def test_extract_results_falls_back_to_anchor_text_when_no_title_attribute():
    anchor = FakeAnchor(href="/thing/p/itm1", title=None, text="Wireless Earbuds", container_text="")
    results = flipkart_tool._extract_results(FakePage([anchor]))
    assert results[0]["title"] == "Wireless Earbuds"


def test_extract_results_skips_anchor_with_no_usable_title():
    anchor = FakeAnchor(href="/thing/p/itm1", title=None, text="", container_text="")
    results = flipkart_tool._extract_results(FakePage([anchor]))
    assert results == []


def test_extract_results_deduplicates_repeated_hrefs():
    a = make_anchor(href="/thing/p/itm1")
    b = make_anchor(href="/thing/p/itm1")
    results = flipkart_tool._extract_results(FakePage([a, b]))
    assert len(results) == 1


def test_extract_results_tolerates_missing_price_and_rating():
    anchor = make_anchor(container_text="no numbers here")
    results = flipkart_tool._extract_results(FakePage([anchor]))
    assert results == [
        {"title": "Phone X", "price": None, "rating": None, "link": "https://www.flipkart.com/some-phone/p/itm123"}
    ]


def test_extract_results_caps_at_max_results():
    anchors = [make_anchor(href=f"/thing/p/itm{i}") for i in range(15)]
    results = flipkart_tool._extract_results(FakePage(anchors))
    assert len(results) == flipkart_tool._MAX_RESULTS


# --- price filtering (client-side only, no server-side URL param) ---


def test_parse_price_extracts_number():
    assert flipkart_tool._parse_price("₹1,490") == 1490.0


def test_parse_price_returns_none_for_unparseable():
    assert flipkart_tool._parse_price(None) is None
    assert flipkart_tool._parse_price("Contact seller") is None


def test_filter_by_price_drops_out_of_range_and_unparseable():
    results = [
        {"title": "A", "price": "₹500", "rating": None, "link": "x"},
        {"title": "B", "price": "₹2,000", "rating": None, "link": "y"},
        {"title": "C", "price": None, "rating": None, "link": "z"},
    ]
    kept = flipkart_tool._filter_by_price(results, min_price=None, max_price=1000)
    assert [r["title"] for r in kept] == ["A"]


def test_format_results_includes_all_fields():
    formatted = flipkart_tool._format_results(
        [{"title": "Phone X", "price": "₹19,999", "rating": "4.3", "link": "https://flipkart.com/x"}]
    )
    assert "Phone X" in formatted
    assert "₹19,999" in formatted
    assert "https://flipkart.com/x" in formatted


# --- ShopFlipkartTool.run ---


def test_run_returns_disabled_message_when_live_ai_disabled():
    with patch.object(flipkart_tool.config, "live_ai_enabled", False):
        result = ShopFlipkartTool().run(query="phones")
    assert "disabled" in result.lower()


def test_run_returns_setup_message_when_browser_unavailable():
    with patch.object(flipkart_tool.config, "live_ai_enabled", True), patch.object(
        flipkart_tool.browser, "available", return_value=False
    ):
        result = ShopFlipkartTool().run(query="phones")
    assert "isn't set up" in result


def test_run_rejects_inverted_price_range():
    with patch.object(flipkart_tool.config, "live_ai_enabled", True), patch.object(
        flipkart_tool.browser, "available", return_value=True
    ):
        result = ShopFlipkartTool().run(query="phones", min_price=2000, max_price=1000)
    assert "doesn't make sense" in result


def test_run_opens_homepage_when_query_empty():
    page = FakePage([])
    with patch.object(flipkart_tool.config, "live_ai_enabled", True), patch.object(
        flipkart_tool.browser, "available", return_value=True
    ), patch.object(flipkart_tool.browser, "get_context", return_value=FakeContext(page)):
        result = ShopFlipkartTool().run(query="")
    assert page.url == "https://www.flipkart.com"
    assert "Opened Flipkart" in result


def test_run_reports_no_listings_honestly():
    page = FakePage([])
    with patch.object(flipkart_tool.config, "live_ai_enabled", True), patch.object(
        flipkart_tool.browser, "available", return_value=True
    ), patch.object(flipkart_tool.browser, "get_context", return_value=FakeContext(page)):
        result = ShopFlipkartTool().run(query="something obscure")
    assert "couldn't read any listings" in result


def test_run_success_lists_results_and_states_research_only():
    page = FakePage([make_anchor()])
    with patch.object(flipkart_tool.config, "live_ai_enabled", True), patch.object(
        flipkart_tool.browser, "available", return_value=True
    ), patch.object(flipkart_tool.browser, "get_context", return_value=FakeContext(page)):
        result = ShopFlipkartTool().run(query="phone")
    assert "Phone X" in result
    assert "research only" in result
    assert "can't add these to a cart" in result


def test_run_reports_when_price_filter_matches_nothing():
    page = FakePage([make_anchor(container_text="₹99,999")])
    with patch.object(flipkart_tool.config, "live_ai_enabled", True), patch.object(
        flipkart_tool.browser, "available", return_value=True
    ), patch.object(flipkart_tool.browser, "get_context", return_value=FakeContext(page)):
        result = ShopFlipkartTool().run(query="phone", max_price=1000)
    assert "none confirmed" in result


def test_run_surfaces_unexpected_errors_instead_of_raising():
    with patch.object(flipkart_tool.config, "live_ai_enabled", True), patch.object(
        flipkart_tool.browser, "available", return_value=True
    ), patch.object(flipkart_tool.browser, "get_context", side_effect=RuntimeError("boom")):
        result = ShopFlipkartTool().run(query="phone")
    assert "Something went wrong" in result

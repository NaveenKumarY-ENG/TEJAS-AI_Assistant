"""
Tests for tools/shopping_tool.py. No real browser/Amazon involved — a tiny
fake Playwright-shaped Page/element stand-in exercises the extraction and
CAPTCHA-detection logic directly (see integrations/browser.py's own
availability check for what IS real: whether Chromium is actually
installed, which these tests don't touch at all).
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import shopping_tool
from tools.shopping_tool import ShopAmazonTool


class FakeElement:
    def __init__(self, text: str = "", attrs: dict | None = None):
        self._text = text
        self._attrs = attrs or {}

    def inner_text(self) -> str:
        return self._text

    def get_attribute(self, name: str):
        return self._attrs.get(name)


class FakeCard:
    def __init__(self, elements: dict):
        self._elements = elements

    def query_selector(self, selector: str):
        return self._elements.get(selector)


def make_card(title="Phone X", price="$199.99", rating="4.5 out of 5 stars", href="/dp/B000TEST"):
    return FakeCard(
        {
            "h2 span": FakeElement(text=title) if title else None,
            ".a-price .a-offscreen": FakeElement(text=price) if price else None,
            ".a-icon-alt": FakeElement(text=rating) if rating else None,
            "h2 a": FakeElement(attrs={"href": href}) if href else None,
        }
    )


class FakePage:
    def __init__(self, cards, body_text: str = ""):
        self._cards = cards
        self._body_text = body_text

    def query_selector_all(self, selector: str):
        assert selector == '[data-component-type="s-search-result"]'
        return self._cards

    def goto(self, url, wait_until=None):
        self.url = url

    def bring_to_front(self):
        pass

    def wait_for_selector(self, selector, timeout=None):
        if not self._cards:
            raise TimeoutError("no results")

    def inner_text(self, selector):
        assert selector == "body"
        return self._body_text


class FakeContext:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page


def test_extract_results_parses_title_price_rating_link():
    results = shopping_tool._extract_results(FakePage([make_card()]))
    assert results == [
        {
            "title": "Phone X",
            "price": "$199.99",
            "rating": "4.5 out of 5 stars",
            "link": "https://www.amazon.com/dp/B000TEST",
        }
    ]


def test_extract_results_falls_back_to_a_link_normal_when_no_h2_anchor():
    """Confirmed live: Amazon's current markup has h2 as a standalone
    element (just a <span> inside) with the real product link as a
    SEPARATE sibling <a class="a-link-normal">, not nested inside h2 at
    all — the h2-only card (no "h2 a" entry) exercises exactly that layout."""
    card = FakeCard(
        {
            "h2 span": FakeElement(text="Phone X"),
            "a.a-link-normal": FakeElement(attrs={"href": "/dp/B000TEST"}),
        }
    )
    results = shopping_tool._extract_results(FakePage([card]))
    assert results[0]["link"] == "https://www.amazon.com/dp/B000TEST"


def test_extract_results_skips_card_with_no_title():
    results = shopping_tool._extract_results(FakePage([make_card(title=None)]))
    assert results == []


def test_extract_results_tolerates_missing_price_and_rating():
    results = shopping_tool._extract_results(FakePage([make_card(price=None, rating=None)]))
    assert results == [{"title": "Phone X", "price": None, "rating": None, "link": "https://www.amazon.com/dp/B000TEST"}]


def test_extract_results_caps_at_max_results():
    results = shopping_tool._extract_results(FakePage([make_card() for _ in range(10)]))
    assert len(results) == shopping_tool._MAX_RESULTS


def test_format_results_includes_all_fields():
    formatted = shopping_tool._format_results(
        [{"title": "Phone X", "price": "$199.99", "rating": "4.5 out of 5 stars", "link": "https://amazon.com/x"}]
    )
    assert "Phone X" in formatted
    assert "$199.99" in formatted
    assert "https://amazon.com/x" in formatted


def test_run_returns_setup_message_when_browser_unavailable():
    with patch.object(shopping_tool.browser, "available", return_value=False):
        result = ShopAmazonTool().run("phones")
    assert "isn't set up" in result


def test_run_detects_captcha_challenge():
    fake_page = FakePage(cards=[], body_text="Enter the characters you see below")
    with (
        patch.object(shopping_tool.browser, "available", return_value=True),
        patch.object(shopping_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = ShopAmazonTool().run("phones")
    assert "verification challenge" in result


def test_run_returns_formatted_results_on_success():
    fake_page = FakePage(cards=[make_card()])
    with (
        patch.object(shopping_tool.browser, "available", return_value=True),
        patch.object(shopping_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = ShopAmazonTool().run("phones")
    assert "Phone X" in result
    assert "https://www.amazon.com/dp/B000TEST" in result


def test_run_reports_unreadable_layout_when_no_cards_and_no_captcha():
    fake_page = FakePage(cards=[], body_text="Amazon.com : phones")
    with (
        patch.object(shopping_tool.browser, "available", return_value=True),
        patch.object(shopping_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = ShopAmazonTool().run("phones")
    assert "couldn't read any listings" in result

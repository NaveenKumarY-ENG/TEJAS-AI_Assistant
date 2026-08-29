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

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import shopping_tool
from tools.shopping_tool import ShopAmazonTool


@pytest.fixture(autouse=True)
def _clean_known_urls():
    """_known_product_urls is module-level state (see its own comment for
    why — a real, deliberately global, shared-browser cache). Isolate each
    test from whatever an earlier one left behind, same reasoning as any
    other shared-state test fixture in this suite."""
    shopping_tool._known_product_urls.clear()
    yield
    shopping_tool._known_product_urls.clear()


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
            "link": "https://www.amazon.in/dp/B000TEST",
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
    assert results[0]["link"] == "https://www.amazon.in/dp/B000TEST"


def test_extract_results_skips_card_with_no_title():
    results = shopping_tool._extract_results(FakePage([make_card(title=None)]))
    assert results == []


def test_extract_results_tolerates_missing_price_and_rating():
    results = shopping_tool._extract_results(FakePage([make_card(price=None, rating=None)]))
    assert results == [{"title": "Phone X", "price": None, "rating": None, "link": "https://www.amazon.in/dp/B000TEST"}]


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


def test_run_with_no_query_opens_the_homepage_not_a_search():
    """Regression test for a real gap found live: "open Amazon"/"open
    amazon.in" with nothing to search for yet used to require a query
    string, and an empty one navigated to a *search* for nothing
    (amazon.in/s?k=) — which then reported the confusing, misleading
    "couldn't read any listings ... Amazon may have changed its layout,"
    even though nothing was actually wrong. query is optional now, and an
    empty/omitted one opens the plain homepage with a clear message
    instead of pretending to search."""
    fake_page = FakePage(cards=[])
    with (
        patch.object(shopping_tool.browser, "available", return_value=True),
        patch.object(shopping_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result_no_arg = ShopAmazonTool().run()
        result_empty = ShopAmazonTool().run(query="")
        result_whitespace = ShopAmazonTool().run(query="   ")
    for result in (result_no_arg, result_empty, result_whitespace):
        assert "opened amazon.in" in result.lower()
        assert "couldn't read" not in result.lower()
    assert fake_page.url == "https://www.amazon.in"


def test_run_detects_captcha_challenge():
    fake_page = FakePage(cards=[], body_text="Enter the characters you see below")
    with (
        patch.object(shopping_tool.browser, "available", return_value=True),
        patch.object(shopping_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = ShopAmazonTool().run("phones")
    assert "verification challenge" in result


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
    assert "https://www.amazon.in/dp/B000TEST" in result


def test_run_reports_unreadable_layout_when_no_cards_and_no_captcha():
    fake_page = FakePage(cards=[], body_text="Amazon.com : phones")
    with (
        patch.object(shopping_tool.browser, "available", return_value=True),
        patch.object(shopping_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = ShopAmazonTool().run("phones")
    assert "couldn't read any listings" in result


def test_a_url_from_a_real_search_becomes_known():
    """Regression coverage for the fix in order_tool.py: order_amazon must
    be able to verify a product_url actually came from a real shop_amazon
    result. This is the producing side of that check."""
    assert shopping_tool.is_known_product_url("https://www.amazon.in/dp/B000TEST") is False
    fake_page = FakePage(cards=[make_card()])
    with (
        patch.object(shopping_tool.browser, "available", return_value=True),
        patch.object(shopping_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        ShopAmazonTool().run("phones")
    assert shopping_tool.is_known_product_url("https://www.amazon.in/dp/B000TEST") is True


def test_an_unrelated_url_never_becomes_known():
    fake_page = FakePage(cards=[make_card()])
    with (
        patch.object(shopping_tool.browser, "available", return_value=True),
        patch.object(shopping_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        ShopAmazonTool().run("phones")
    assert shopping_tool.is_known_product_url("https://www.amazon.in/dp/SOMETHING-ELSE") is False


def test_known_urls_are_bounded_by_count():
    """_known_product_urls is capped, not unbounded — see its own comment
    on why (long-running server, no natural expiry otherwise)."""
    results = [{"title": f"Item {i}", "price": None, "rating": None, "link": f"https://www.amazon.in/dp/{i}"} for i in range(shopping_tool._MAX_KNOWN_URLS + 10)]
    shopping_tool._remember_results(results)
    assert len(shopping_tool._known_product_urls) == shopping_tool._MAX_KNOWN_URLS
    # oldest entries evicted first
    assert shopping_tool.is_known_product_url("https://www.amazon.in/dp/0") is False
    assert shopping_tool.is_known_product_url(f"https://www.amazon.in/dp/{shopping_tool._MAX_KNOWN_URLS + 9}") is True


def test_parse_price_handles_rupee_formatting():
    assert shopping_tool._parse_price("₹1,490") == 1490.0
    assert shopping_tool._parse_price("₹849") == 849.0
    assert shopping_tool._parse_price("₹1,23,456") == 123456.0  # Indian digit grouping


def test_parse_price_returns_none_for_unparseable_or_missing():
    assert shopping_tool._parse_price(None) is None
    assert shopping_tool._parse_price("") is None
    assert shopping_tool._parse_price("Currently unavailable") is None


def test_price_range_param_matches_amazons_own_format():
    """Confirmed live directly from Amazon.in's own generated filter-
    sidebar links, not guessed — see the function's own docstring."""
    assert shopping_tool._price_range_param(None, 350) == "p_36:-35000"
    assert shopping_tool._price_range_param(350, 600) == "p_36:35000-60000"
    assert shopping_tool._price_range_param(1800, None) == "p_36:180000-"
    assert shopping_tool._price_range_param(None, None) is None


def test_filter_by_price_drops_out_of_range_and_unparseable():
    results = [
        {"title": "Cheap", "price": "₹500", "rating": None, "link": "a"},
        {"title": "Mid", "price": "₹1,000", "rating": None, "link": "b"},
        {"title": "Expensive", "price": "₹2,000", "rating": None, "link": "c"},
        {"title": "Unknown price", "price": None, "rating": None, "link": "d"},
    ]
    kept = shopping_tool._filter_by_price(results, min_price=None, max_price=1000)
    assert [r["title"] for r in kept] == ["Cheap", "Mid"]  # unparseable dropped, not assumed in-range


def test_run_with_max_price_only_returns_only_in_budget_results():
    """Regression test for the actual reported problem: a free-text "under
    1000" query returned real results up to ₹1,490 — Amazon's own search
    treats a price phrase as a soft ranking hint, not a hard filter. With
    max_price passed as a real parameter, every returned result must
    actually be confirmed within budget."""
    cards = [make_card(title="A", price="₹959", href="/dp/A"), make_card(title="B", price="₹1,490", href="/dp/B")]
    fake_page = FakePage(cards=cards)
    with (
        patch.object(shopping_tool.browser, "available", return_value=True),
        patch.object(shopping_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = ShopAmazonTool().run(query="headphones", max_price=1000)
    assert "A" in result
    assert "B" not in result
    assert "rh=p_36" in fake_page.url  # the real Amazon-side filter was actually applied too


def test_run_with_min_and_max_price_range():
    cards = [
        make_card(title="TooCheap", price="₹300", href="/dp/1"),
        make_card(title="JustRight", price="₹800", href="/dp/2"),
        make_card(title="TooExpensive", price="₹2000", href="/dp/3"),
    ]
    fake_page = FakePage(cards=cards)
    with (
        patch.object(shopping_tool.browser, "available", return_value=True),
        patch.object(shopping_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = ShopAmazonTool().run(query="headphones", min_price=500, max_price=1500)
    assert "JustRight" in result
    assert "TooCheap" not in result
    assert "TooExpensive" not in result
    assert "between ₹500 and ₹1500" in result


def test_run_reports_when_nothing_confirmed_in_budget():
    cards = [make_card(title="Pricey", price="₹5000", href="/dp/1")]
    fake_page = FakePage(cards=cards)
    with (
        patch.object(shopping_tool.browser, "available", return_value=True),
        patch.object(shopping_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = ShopAmazonTool().run(query="headphones", max_price=1000)
    assert "none confirmed" in result.lower()
    assert "Pricey" not in result


def test_run_rejects_an_inverted_price_range():
    with patch.object(shopping_tool.browser, "available", return_value=True):
        result = ShopAmazonTool().run(query="headphones", min_price=5000, max_price=100)
    assert "doesn't make sense" in result.lower()


def test_looks_like_amazon_url_accepts_real_product_links():
    assert shopping_tool.looks_like_amazon_url("https://www.amazon.in/dp/B0CTESTXXX") is True
    assert shopping_tool.looks_like_amazon_url("https://amazon.in/dp/B0CTESTXXX") is True
    assert shopping_tool.looks_like_amazon_url("https://www.amazon.in/Some-Product-Slug/dp/B0CTESTXXX/ref=sr_1_1") is True
    assert shopping_tool.looks_like_amazon_url("https://www.amazon.in/gp/product/B0CTESTXXX") is True


def test_looks_like_amazon_url_accepts_amazons_own_short_links():
    """The Amazon app's "Share" button generates amzn.in/amzn.to links, not
    a full amazon.in/dp/... URL — the ASIN shape can't be checked on an
    opaque short code, so these are trusted on domain alone (order_tool.py
    still confirms live, after following the redirect, that a real product
    page actually loaded)."""
    assert shopping_tool.looks_like_amazon_url("https://amzn.in/d/abc123") is True
    assert shopping_tool.looks_like_amazon_url("https://amzn.to/abc123") is True


def test_looks_like_amazon_url_accepts_a_sponsored_click_through_link():
    """Regression test for a real gap found live: a sponsored/ad listing's
    own link — /sspa/click?...&url=<url-encoded real product path>... —
    is a completely legitimate, common Amazon.in link (2-3 of every 5
    search results are routinely sponsored, and shopping_tool.py's own
    _extract_results captures exactly this as their `link`), but its
    visible path is /sspa/click, not /dp/... or /gp/product/..., so the
    plain shape check alone rejected it as if it weren't really Amazon."""
    sponsored = (
        "https://www.amazon.in/sspa/click?ie=UTF8&spc=abc123"
        "&url=%2FOnePlus-Snapdragon-Segments-Fastest-Response%2Fdp%2FB0GWLV615M%2Fref%3Dsr_1_1_sspa"
        "&aref=xyz"
    )
    assert shopping_tool.looks_like_amazon_url(sponsored) is True


def test_looks_like_amazon_url_rejects_non_product_and_non_amazon_links():
    assert shopping_tool.looks_like_amazon_url("https://www.amazon.in/") is False  # bare homepage, no product
    assert shopping_tool.looks_like_amazon_url("https://www.amazon.in/s?k=phones") is False  # a search, not a product
    assert shopping_tool.looks_like_amazon_url("https://example.com/dp/B0CTESTXXX") is False  # not Amazon at all
    assert shopping_tool.looks_like_amazon_url("http://www.amazon.in/dp/B0CTESTXXX") is False  # not https
    assert shopping_tool.looks_like_amazon_url("") is False
    assert shopping_tool.looks_like_amazon_url(None) is False


def test_search_products_reports_captcha_and_no_listings_distinctly():
    captcha_page = FakePage(cards=[], body_text="Enter the characters you see below")
    with (
        patch.object(shopping_tool.browser, "get_context", return_value=FakeContext(captcha_page)),
    ):
        assert shopping_tool.search_products("phones") == {"captcha": True}

    empty_page = FakePage(cards=[], body_text="Amazon.in : phones")
    with (
        patch.object(shopping_tool.browser, "get_context", return_value=FakeContext(empty_page)),
    ):
        assert shopping_tool.search_products("phones") == {"no_listings": True}


def test_search_products_returns_and_remembers_results():
    fake_page = FakePage(cards=[make_card()])
    with patch.object(shopping_tool.browser, "get_context", return_value=FakeContext(fake_page)):
        outcome = shopping_tool.search_products("phones")
    assert outcome["results"][0]["title"] == "Phone X"
    assert shopping_tool.is_known_product_url("https://www.amazon.in/dp/B000TEST") is True

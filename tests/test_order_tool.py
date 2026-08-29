"""
Tests for tools/order_tool.py (Amazon Tier 2 — cart + checkout review, never
completes a purchase). No real browser/Amazon involved — a fake
Playwright-shaped Page stand-in exercises each step's logic directly, same
approach as tests/test_shopping_tool.py.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import order_tool, shopping_tool
from tools.order_tool import OrderAmazonTool

_TEST_URL = "https://www.amazon.in/dp/B000TEST"


@pytest.fixture(autouse=True)
def _clean_known_urls():
    """Same reasoning as test_shopping_tool.py's fixture of the same name —
    _known_product_urls is real shared module state (see its own comment)."""
    shopping_tool._known_product_urls.clear()
    yield
    shopping_tool._known_product_urls.clear()


class FakeElement:
    def __init__(self, text: str = "", visible: bool = True):
        self._text = text
        self.clicked = False
        self._visible = visible

    def inner_text(self) -> str:
        return self._text

    def click(self) -> None:
        self.clicked = True

    def is_visible(self) -> bool:
        return self._visible


class FakePage:
    def __init__(
        self,
        logged_in: bool = True,
        has_add_to_cart: bool = True,
        cart_updates_after_click: bool = True,
        checkout_reachable: bool = True,
        body_text: str = "Secure checkout\nOrder Total:\n₹19.99\nplace your order",
        has_total: bool = True,
        current_price: str | None = "₹959.00",
        has_product_title: bool = True,
        product_title: str = "Test Product",
    ):
        self._account_el = FakeElement("Hello, Naveen" if logged_in else "Hello, sign in")
        self._add_to_cart_el = FakeElement() if has_add_to_cart else None
        self._cart_updates_after_click = cart_updates_after_click
        self._cart_count = "0"
        self._checkout_reachable = checkout_reachable
        self._body_text = body_text if has_total else body_text.split("Order Total:")[0]
        self._price_el = FakeElement(current_price) if current_price else None
        self._title_el = FakeElement(product_title) if has_product_title else None
        self.urls_visited = []
        self.url = ""

    def goto(self, url, wait_until=None):
        self.urls_visited.append(url)
        self.url = url

    def bring_to_front(self):
        pass

    def query_selector(self, selector: str):
        if selector == "#nav-link-accountList-nav-line-1":
            return self._account_el
        if selector == "#add-to-cart-button":
            return self._add_to_cart_el
        if selector == "#nav-cart-count":
            return FakeElement(self._cart_count)
        if selector == ".a-price .a-offscreen":
            return self._price_el
        if selector == "#productTitle":
            return self._title_el
        return None

    def query_selector_all(self, selector: str):
        if selector == "#add-to-cart-button":
            return [self._add_to_cart_el] if self._add_to_cart_el else []
        return []

    def wait_for_selector(self, selector, timeout=None):
        if selector == "input[name='proceedToRetailCheckout']" and not self._checkout_reachable:
            raise TimeoutError("not found")

    def wait_for_function(self, expression, arg=None, timeout=None):
        # Mirrors the real Playwright call in _add_to_cart: succeeds (cart
        # count "changed") unless the fixture says the click didn't register.
        if self._cart_updates_after_click:
            self._cart_count = "1"
            return
        raise TimeoutError("cart count never changed")

    def click(self, selector: str, timeout=None):
        # "Continue to checkout" — order_tool.py's upsell-interstitial
        # handling. FakePage never simulates that interstitial being
        # present, so this always reports "not found," matching real
        # Playwright's behavior for a selector that matches nothing — the
        # caller's try/except treats that as "no interstitial here," a
        # no-op, same as most real sessions.
        if selector.startswith("a:has-text"):
            raise TimeoutError("not found")

    def wait_for_load_state(self, state, timeout=None):
        pass

    def inner_text(self, selector):
        assert selector == "body"
        return self._body_text


class FakeContext:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page


def _known_url():
    """Every test that exercises OrderAmazonTool.run() past the URL-
    validation guard needs the URL to look like a real shop_amazon result —
    see shopping_tool.py's _known_product_urls. Patches the check directly
    rather than running a real search, matching this file's existing
    approach of testing order_tool's own logic in isolation."""
    return patch("tools.order_tool.shopping_tool.is_known_product_url", return_value=True)


def test_is_logged_in_true_for_real_name():
    assert order_tool._is_logged_in(FakePage(logged_in=True)) is True


def test_is_logged_in_false_when_signed_out():
    assert order_tool._is_logged_in(FakePage(logged_in=False)) is False


def test_is_logged_in_false_when_account_element_missing():
    page = FakePage()
    page.query_selector = lambda selector: None
    assert order_tool._is_logged_in(page) is False


def test_add_to_cart_clicks_button_and_confirms_via_cart_count():
    page = FakePage(has_add_to_cart=True, cart_updates_after_click=True)
    assert order_tool._add_to_cart(page) is True
    assert page._add_to_cart_el.clicked is True


def test_add_to_cart_returns_false_when_button_missing():
    page = FakePage(has_add_to_cart=False)
    assert order_tool._add_to_cart(page) is False


def test_add_to_cart_returns_false_when_click_never_updates_cart_count():
    """Regression test for a real gap: the old version returned True the
    moment .click() didn't raise, even though a click succeeding isn't the
    same as the item actually landing in the cart (a variant-selector
    popup, a promo interstitial, or a layout change can all swallow it
    silently). Waiting for Amazon's own cart-count badge to actually change
    is a real confirmation, not just "nothing threw.\""""
    page = FakePage(has_add_to_cart=True, cart_updates_after_click=False)
    assert order_tool._add_to_cart(page) is False
    assert page._add_to_cart_el.clicked is True  # the click itself did happen


def test_go_to_checkout_succeeds_when_reachable():
    page = FakePage(checkout_reachable=True)
    assert order_tool._go_to_checkout(page) is True
    assert "amazon.in/gp/cart/view.html" in page.urls_visited[0]


def test_go_to_checkout_fails_when_proceed_button_never_appears():
    page = FakePage(checkout_reachable=False)
    assert order_tool._go_to_checkout(page) is False


def test_extract_checkout_summary_returns_total_when_present():
    """Confirmed live: Amazon's real checkout flow lands on a "Secure
    checkout" page with delivery address, order total, and payment method
    selection — NOT the older "place your order" page directly (that only
    appears after a payment method is chosen, a step this app must never
    take, see this file's own module docstring). The order total's own
    text is the actual confirmation, pulled straight from the page rather
    than a selector that turned out to match several different summary
    rows (Items/Delivery/Order Total/etc.) once this page's markup was
    checked live."""
    page = FakePage(body_text="Secure checkout\nOrder Total:\n₹19.99", has_total=True)
    assert order_tool._extract_checkout_summary(page) == "Order total: ₹19.99"


def test_extract_checkout_summary_none_without_a_confirmable_total():
    """No confirmable "Order Total: ₹X" text means no claim of success —
    same "don't claim what wasn't verified" principle used throughout this
    codebase, rather than a generic "reached checkout" guess."""
    page = FakePage(body_text="Secure checkout", has_total=False)
    assert order_tool._extract_checkout_summary(page) is None


def test_extract_checkout_summary_none_when_not_on_review_page():
    page = FakePage(body_text="your cart is empty", has_total=False)
    assert order_tool._extract_checkout_summary(page) is None


def test_run_returns_setup_message_when_browser_unavailable():
    with patch.object(order_tool.browser, "available", return_value=False):
        result = OrderAmazonTool().run(_TEST_URL)
    assert "isn't set up" in result


def test_run_requires_a_url_or_a_name():
    with patch.object(order_tool.browser, "available", return_value=True):
        result = OrderAmazonTool().run()
    assert "need either" in result.lower()


def test_run_rejects_a_link_that_isnt_amazon_at_all():
    """Regression test for a real gap found live: order_amazon used to
    navigate to whatever URL string it was given, with no check that it
    was ever an actual shop_amazon result — trusting a local model to
    reproduce a long URL verbatim from earlier in the conversation, the
    same class of unreliability already confirmed elsewhere in this
    codebase (garbled filenames, altered table values). A genuinely
    non-Amazon link must still be rejected outright, before ever
    navigating there — direct-link support (see
    test_run_accepts_a_pasted_link_never_seen_in_a_search below) only
    extends trust to real Amazon.in/amzn.in links, not anywhere."""
    with patch.object(order_tool.browser, "available", return_value=True):
        result = OrderAmazonTool().run(product_url="https://example.com/totally-unrelated")
    assert "doesn't look like a real amazon.in product link" in result.lower()


def test_run_accepts_a_pasted_link_never_seen_in_a_search():
    """The user directly sharing a real Amazon.in product link should work
    even though it never came from a shop_amazon call in this conversation
    — trusted differently than a URL the model would otherwise have to
    recall from memory (see shopping_tool.looks_like_amazon_url). Uses a
    real-shaped 10-char ASIN — _TEST_URL's is deliberately shorter, which
    is fine for is_known_product_url's exact-match cache lookup but would
    fail looks_like_amazon_url's shape check, so it doesn't fit this test."""
    shared_url = "https://www.amazon.in/dp/B0CTESTXXX"
    fake_page = FakePage(product_title="Shared Link Product")
    with (
        patch("tools.order_tool.shopping_tool.is_known_product_url", return_value=False),
        patch.object(order_tool.browser, "available", return_value=True),
        patch.object(order_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = OrderAmazonTool().run(product_url=shared_url)
    assert "shared link product" in result.lower()
    assert "added" in result.lower()


def test_run_rejects_a_link_whose_page_isnt_actually_a_product():
    """Even a link that passes the domain/shape check gets confirmed live —
    a hallucinated-but-valid-looking ASIN, a removed listing, or a short
    link redirecting somewhere unexpected all fail here instead of
    silently proceeding."""
    fake_page = FakePage(has_product_title=False)
    with (
        _known_url(),
        patch.object(order_tool.browser, "available", return_value=True),
        patch.object(order_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = OrderAmazonTool().run(_TEST_URL)
    assert "doesn't look like a real amazon product page" in result.lower()


def test_run_resolves_a_product_name_via_a_live_search():
    """Requirement: giving a full product name/title instead of a link
    (e.g. read out loud, or pasted from an Amazon listing) should still
    work — resolved via a real search, matching the closest title rather
    than blindly the first result."""
    fake_page = FakePage(product_title="iQOO Z11 5G (Celestial Blue, 8GB RAM, 128GB Storage)")
    search_outcome = {
        "results": [
            {
                "title": "iQOO Z11 5G (Celestial Blue, 8GB RAM, 128GB Storage) | India's 1st MediaTek Dimensity 7500 Turbo",
                "price": "₹19,999",
                "rating": None,
                "link": _TEST_URL,
            },
            {"title": "Unrelated USB Cable 1m", "price": "₹299", "rating": None, "link": "https://www.amazon.in/dp/OTHERONE0"},
        ]
    }
    with (
        patch.object(order_tool.browser, "available", return_value=True),
        patch.object(order_tool.browser, "get_context", return_value=FakeContext(fake_page)),
        patch("tools.order_tool.shopping_tool.search_products", return_value=search_outcome) as mock_search,
    ):
        result = OrderAmazonTool().run(product_name="iQOO Z11 5G (Celestial Blue, 8GB RAM, 128GB Storage)")
    mock_search.assert_called_once_with("iQOO Z11 5G (Celestial Blue, 8GB RAM, 128GB Storage)")
    assert fake_page.urls_visited[0] == _TEST_URL
    assert "iqoo z11" in result.lower()


def test_run_reports_when_product_name_search_finds_nothing():
    with (
        patch.object(order_tool.browser, "available", return_value=True),
        patch("tools.order_tool.shopping_tool.search_products", return_value={"results": []}),
    ):
        result = OrderAmazonTool().run(product_name="some nonexistent gadget")
    assert "couldn't find" in result.lower()


def test_run_reports_captcha_hit_while_resolving_a_product_name():
    with (
        patch.object(order_tool.browser, "available", return_value=True),
        patch("tools.order_tool.shopping_tool.search_products", return_value={"captcha": True}),
    ):
        result = OrderAmazonTool().run(product_name="some phone")
    assert "verification challenge" in result.lower()


def test_run_reports_not_logged_in():
    fake_page = FakePage(logged_in=False)
    with (
        _known_url(),
        patch.object(order_tool.browser, "available", return_value=True),
        patch.object(order_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = OrderAmazonTool().run(_TEST_URL)
    assert "not logged into amazon" in result.lower()


def test_run_reports_missing_add_to_cart_button():
    fake_page = FakePage(logged_in=True, has_add_to_cart=False)
    with (
        _known_url(),
        patch.object(order_tool.browser, "available", return_value=True),
        patch.object(order_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = OrderAmazonTool().run(_TEST_URL)
    assert "couldn't confirm the item was actually added" in result.lower()


def test_run_reports_unreachable_checkout():
    fake_page = FakePage(logged_in=True, has_add_to_cart=True, checkout_reachable=False)
    with (
        _known_url(),
        patch.object(order_tool.browser, "available", return_value=True),
        patch.object(order_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = OrderAmazonTool().run(_TEST_URL)
    assert "couldn't reach checkout" in result.lower()


def test_run_reports_unconfirmed_review_page():
    fake_page = FakePage(logged_in=True, has_add_to_cart=True, checkout_reachable=True, body_text="something else")
    with (
        _known_url(),
        patch.object(order_tool.browser, "available", return_value=True),
        patch.object(order_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = OrderAmazonTool().run(_TEST_URL)
    assert "couldn't confirm" in result.lower()


def test_run_success_never_mentions_completing_purchase():
    fake_page = FakePage(logged_in=True, has_add_to_cart=True, checkout_reachable=True)
    with (
        _known_url(),
        patch.object(order_tool.browser, "available", return_value=True),
        patch.object(order_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = OrderAmazonTool().run(_TEST_URL)
    assert "checkout review page" in result.lower()
    assert "place your order" in result.lower()
    assert "never complete a purchase" in result.lower()
    # the tool must never itself claim the purchase is done
    assert "purchase complete" not in result.lower()
    assert "order placed" not in result.lower()


def test_run_success_message_includes_name_price_link_and_added_confirmation():
    """Requirement: adding by name/link should tell the user what was
    actually added — its name, price, link, and an explicit added-to-cart
    confirmation — not just the checkout summary."""
    fake_page = FakePage(
        logged_in=True, has_add_to_cart=True, checkout_reachable=True,
        product_title="Test Product", current_price="₹959.00",
    )
    with (
        _known_url(),
        patch.object(order_tool.browser, "available", return_value=True),
        patch.object(order_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = OrderAmazonTool().run(_TEST_URL)
    assert "added" in result.lower()
    assert "test product" in result.lower()
    assert "₹959.00" in result
    assert _TEST_URL in result


def test_price_change_note_flags_a_real_difference():
    """Regression coverage for a real, worthwhile addition: Amazon prices
    move constantly between when a user searches and when they actually
    order — the tool should say so rather than silently letting the user
    proceed to checkout on stale price information."""
    shopping_tool._known_product_urls[_TEST_URL] = {"price": "₹959"}
    page = FakePage(current_price="₹1,299.00")
    note = order_tool._price_change_note(page, _TEST_URL)
    assert "₹959" in note
    assert "₹1,299.00" in note


def test_price_change_note_silent_when_price_is_the_same():
    shopping_tool._known_product_urls[_TEST_URL] = {"price": "₹959"}
    page = FakePage(current_price="₹959.00")  # same value, just formatted differently
    assert order_tool._price_change_note(page, _TEST_URL) == ""


def test_price_change_note_silent_when_either_price_unknown():
    page = FakePage(current_price="₹959.00")
    # nothing seeded in _known_product_urls for this URL
    assert order_tool._price_change_note(page, _TEST_URL) == ""

    shopping_tool._known_product_urls[_TEST_URL] = {"price": "₹959"}
    page_no_price = FakePage(current_price=None)
    assert order_tool._price_change_note(page_no_price, _TEST_URL) == ""


def test_run_success_message_includes_a_price_change_note():
    shopping_tool._known_product_urls[_TEST_URL] = {"price": "₹959"}
    fake_page = FakePage(logged_in=True, has_add_to_cart=True, checkout_reachable=True, current_price="₹1,299.00")
    with (
        _known_url(),
        patch.object(order_tool.browser, "available", return_value=True),
        patch.object(order_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = OrderAmazonTool().run(_TEST_URL)
    assert "₹959" in result
    assert "₹1,299.00" in result


def test_best_match_picks_the_closest_title_not_just_the_first():
    results = [
        {"title": "Unrelated USB Cable", "price": None, "rating": None, "link": "a"},
        {"title": "iQOO Z11 5G (Celestial Blue, 8GB RAM, 128GB Storage)", "price": None, "rating": None, "link": "b"},
    ]
    match = order_tool._best_match("iQOO Z11 5G (Celestial Blue, 8GB RAM, 128GB Storage)", results)
    assert match["link"] == "b"


def test_best_match_falls_back_to_first_result_when_nothing_scores_well():
    results = [{"title": "Completely unrelated title", "price": None, "rating": None, "link": "only-one"}]
    assert order_tool._best_match("something else entirely", results)["link"] == "only-one"


def test_best_match_returns_none_for_empty_results():
    assert order_tool._best_match("anything", []) is None

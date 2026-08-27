"""
Tests for tools/order_tool.py (Amazon Tier 2 — cart + checkout review, never
completes a purchase). No real browser/Amazon involved — a fake
Playwright-shaped Page stand-in exercises each step's logic directly, same
approach as tests/test_shopping_tool.py.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import order_tool
from tools.order_tool import OrderAmazonTool


class FakeElement:
    def __init__(self, text: str = ""):
        self._text = text
        self.clicked = False

    def inner_text(self) -> str:
        return self._text

    def click(self) -> None:
        self.clicked = True


class FakePage:
    def __init__(
        self,
        logged_in: bool = True,
        has_add_to_cart: bool = True,
        checkout_reachable: bool = True,
        body_text: str = "review your order\nplace your order",
        has_total: bool = True,
    ):
        self._account_el = FakeElement("Hello, Naveen" if logged_in else "Hello, sign in")
        self._add_to_cart_el = FakeElement() if has_add_to_cart else None
        self._checkout_reachable = checkout_reachable
        self._body_text = body_text
        self._total_el = FakeElement("Order total: $19.99") if has_total else None
        self.urls_visited = []

    def goto(self, url, wait_until=None):
        self.urls_visited.append(url)

    def bring_to_front(self):
        pass

    def query_selector(self, selector: str):
        if selector == "#nav-link-accountList-nav-line-1":
            return self._account_el
        if selector == "#add-to-cart-button":
            return self._add_to_cart_el
        if selector == "#subtotals-marketplace-table, .order-summary-line-definition":
            return self._total_el
        return None

    def wait_for_selector(self, selector, timeout=None):
        if selector == "input[name='proceedToRetailCheckout']" and not self._checkout_reachable:
            raise TimeoutError("not found")

    def click(self, selector: str):
        pass

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


def test_is_logged_in_true_for_real_name():
    assert order_tool._is_logged_in(FakePage(logged_in=True)) is True


def test_is_logged_in_false_when_signed_out():
    assert order_tool._is_logged_in(FakePage(logged_in=False)) is False


def test_is_logged_in_false_when_account_element_missing():
    page = FakePage()
    page.query_selector = lambda selector: None
    assert order_tool._is_logged_in(page) is False


def test_add_to_cart_clicks_button_when_present():
    page = FakePage(has_add_to_cart=True)
    assert order_tool._add_to_cart(page) is True
    assert page._add_to_cart_el.clicked is True


def test_add_to_cart_returns_false_when_button_missing():
    page = FakePage(has_add_to_cart=False)
    assert order_tool._add_to_cart(page) is False


def test_go_to_checkout_succeeds_when_reachable():
    page = FakePage(checkout_reachable=True)
    assert order_tool._go_to_checkout(page) is True
    assert "amazon.com/gp/cart/view.html" in page.urls_visited[0]


def test_go_to_checkout_fails_when_proceed_button_never_appears():
    page = FakePage(checkout_reachable=False)
    assert order_tool._go_to_checkout(page) is False


def test_extract_checkout_summary_returns_total_when_present():
    page = FakePage(body_text="place your order", has_total=True)
    assert order_tool._extract_checkout_summary(page) == "Order total: $19.99"


def test_extract_checkout_summary_returns_generic_message_without_total():
    page = FakePage(body_text="place your order", has_total=False)
    assert order_tool._extract_checkout_summary(page) == "Reached checkout review."


def test_extract_checkout_summary_none_when_not_on_review_page():
    page = FakePage(body_text="your cart is empty")
    assert order_tool._extract_checkout_summary(page) is None


def test_run_returns_setup_message_when_browser_unavailable():
    with patch.object(order_tool.browser, "available", return_value=False):
        result = OrderAmazonTool().run("https://www.amazon.com/dp/B000TEST")
    assert "isn't set up" in result


def test_run_reports_not_logged_in():
    fake_page = FakePage(logged_in=False)
    with (
        patch.object(order_tool.browser, "available", return_value=True),
        patch.object(order_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = OrderAmazonTool().run("https://www.amazon.com/dp/B000TEST")
    assert "not logged into amazon" in result.lower()


def test_run_reports_missing_add_to_cart_button():
    fake_page = FakePage(logged_in=True, has_add_to_cart=False)
    with (
        patch.object(order_tool.browser, "available", return_value=True),
        patch.object(order_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = OrderAmazonTool().run("https://www.amazon.com/dp/B000TEST")
    assert "add to cart" in result.lower()


def test_run_reports_unreachable_checkout():
    fake_page = FakePage(logged_in=True, has_add_to_cart=True, checkout_reachable=False)
    with (
        patch.object(order_tool.browser, "available", return_value=True),
        patch.object(order_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = OrderAmazonTool().run("https://www.amazon.com/dp/B000TEST")
    assert "couldn't reach checkout" in result.lower()


def test_run_reports_unconfirmed_review_page():
    fake_page = FakePage(logged_in=True, has_add_to_cart=True, checkout_reachable=True, body_text="something else")
    with (
        patch.object(order_tool.browser, "available", return_value=True),
        patch.object(order_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = OrderAmazonTool().run("https://www.amazon.com/dp/B000TEST")
    assert "couldn't confirm" in result.lower()


def test_run_success_never_mentions_completing_purchase():
    fake_page = FakePage(logged_in=True, has_add_to_cart=True, checkout_reachable=True)
    with (
        patch.object(order_tool.browser, "available", return_value=True),
        patch.object(order_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = OrderAmazonTool().run("https://www.amazon.com/dp/B000TEST")
    assert "checkout review page" in result.lower()
    assert "place your order" in result.lower()
    assert "never complete a purchase" in result.lower()
    # the tool must never itself claim the purchase is done
    assert "purchase complete" not in result.lower()
    assert "order placed" not in result.lower()

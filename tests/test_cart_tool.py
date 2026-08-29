"""
Tests for tools/cart_tool.py (Amazon cart viewing — read-only). No real
browser/Amazon involved — a fake Playwright-shaped Page/element stand-in,
same approach as tests/test_shopping_tool.py and tests/test_order_tool.py.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import cart_tool
from tools.cart_tool import ViewCartTool


class FakeElement:
    def __init__(self, text: str = "", attrs: dict | None = None):
        self._text = text
        self._attrs = attrs or {}

    def inner_text(self) -> str:
        return self._text

    def get_attribute(self, name: str):
        return self._attrs.get(name)


class FakeItemCard:
    def __init__(self, elements: dict, whole_text: str, asin: str | None = None):
        self._elements = elements
        self._whole_text = whole_text
        self._asin = asin

    def query_selector(self, selector: str):
        return self._elements.get(selector)

    def get_attribute(self, name: str):
        return self._asin if name == "data-asin" else None

    def inner_text(self) -> str:
        # Mirrors the real page: the accessible "Quantity is N" text (what
        # _extract_cart_items regex-searches for) is part of the card's
        # overall visible text, not a narrow selector's own inner_text.
        return self._whole_text


def make_item(title="Phone X", price="₹19,999", href="/gp/product/B000TEST", quantity=1, asin="B000TEST"):
    whole_text = f"{title}\n{price}\nQuantity is {quantity}\n{quantity}\n{quantity}" if quantity else f"{title}\n{price}"
    return FakeItemCard(
        {
            ".a-truncate-full": FakeElement(text=title) if title else None,
            ".a-price .a-offscreen": FakeElement(text=price) if price else None,
            "a.sc-product-link": FakeElement(attrs={"href": href}) if href else None,
        },
        whole_text,
        asin=asin,
    )


class FakeSavedContainer:
    def __init__(self, items):
        self._items = items

    def query_selector_all(self, selector: str):
        assert selector == cart_tool._ITEM_SELECTOR
        return self._items


class FakePage:
    def __init__(self, items=None, saved_items=None, logged_in=True, body_text="review your cart", has_saved_container=True):
        # `items` is EVERYTHING the page renders matching _ITEM_SELECTOR —
        # confirmed live this can legitimately span several different
        # active fulfillment/seller groups on one page, not just one. Any
        # of them present in `saved_items` too should be excluded from the
        # result — that's what _extract_cart_items is actually testing.
        self._items = items if items is not None else []
        self._saved_items = saved_items if saved_items is not None else []
        self._account_el = FakeElement("Hello, Naveen" if logged_in else "Hello, sign in")
        self._body_text = body_text
        self._has_saved_container = has_saved_container
        self.urls_visited = []

    def goto(self, url, wait_until=None):
        self.urls_visited.append(url)

    def bring_to_front(self):
        pass

    def query_selector(self, selector: str):
        if selector == "#nav-link-accountList-nav-line-1":
            return self._account_el
        if selector == cart_tool._SAVED_FOR_LATER_CONTAINER:
            return FakeSavedContainer(self._saved_items) if self._has_saved_container else None
        return None

    def query_selector_all(self, selector: str):
        assert selector == cart_tool._ITEM_SELECTOR
        return self._items

    def wait_for_selector(self, selector, timeout=None):
        if not self._items:
            raise TimeoutError("no items")

    def inner_text(self, selector):
        assert selector == "body"
        return self._body_text


class FakeContext:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page


def test_extract_cart_items_parses_title_price_quantity_link():
    page = FakePage(items=[make_item()])
    items = cart_tool._extract_cart_items(page)
    assert items == [
        {"title": "Phone X", "price": "₹19,999", "quantity": "1", "link": "https://www.amazon.in/gp/product/B000TEST"}
    ]


def test_extract_cart_items_skips_a_card_with_no_title():
    page = FakePage(items=[make_item(title=None)])
    assert cart_tool._extract_cart_items(page) == []


def test_extract_cart_items_tolerates_missing_price_and_quantity():
    page = FakePage(items=[make_item(price=None, quantity=None)])
    items = cart_tool._extract_cart_items(page)
    assert items == [{"title": "Phone X", "price": None, "quantity": None, "link": "https://www.amazon.in/gp/product/B000TEST"}]


def test_extract_cart_items_excludes_items_saved_for_later():
    active = make_item(title="Active Item", asin="ACTIVE0001", href="/gp/product/ACTIVE0001")
    saved = make_item(title="Saved Item", asin="SAVED00001", href="/gp/product/SAVED00001")
    page = FakePage(items=[active, saved], saved_items=[saved])
    items = cart_tool._extract_cart_items(page)
    assert [i["title"] for i in items] == ["Active Item"]


def test_extract_cart_items_includes_items_outside_the_one_active_container_it_used_to_assume():
    """Regression test for a real bug found live: an earlier version only
    included items found inside "#activeCartViewForm," which turned out to
    be just ONE of several active fulfillment groups a real account can
    have on the same cart page (Amazon/Amazon Now/Amazon Fresh/Amazon
    Pharmacy, and a separate group for the same product added via a
    different seller offer) — a genuinely active item sitting in any OTHER
    group was silently dropped. Excluding the one reliable "saved for
    later" container (rather than trying to include every active variant)
    is what actually generalizes: this item isn't in `saved_items` at all,
    so it must survive regardless of which group it's rendered under."""
    other_group_item = make_item(title="Different Seller Group Item", asin="OTHERGROUP1", href="/gp/product/OTHERGROUP1")
    page = FakePage(items=[other_group_item], saved_items=[])
    items = cart_tool._extract_cart_items(page)
    assert [i["title"] for i in items] == ["Different Seller Group Item"]


def test_extract_cart_items_falls_back_to_treating_everything_as_active_without_a_saved_container():
    """If Amazon ever renames #sc-saved-cart, this should still report
    every item found rather than silently reporting nothing — erring
    toward showing too much (including something actually saved-for-later)
    rather than dropping something genuinely in the cart, the failure mode
    already found live and fixed above."""
    page = FakePage(items=[make_item()], has_saved_container=False)
    items = cart_tool._extract_cart_items(page)
    assert len(items) == 1


def test_extract_cart_items_collapses_a_title_with_an_embedded_newline():
    """Regression test for a real bug found live: Amazon's title element
    can render an off-screen full-text copy stacked with a separately
    visible truncated copy, and .inner_text() on the wrong container
    returns both joined by a newline — which broke this tool's one-line-
    per-item output. _text()'s whitespace collapsing is the safety net."""
    card = FakeItemCard(
        {
            ".a-truncate-full": FakeElement(text="Full Title\nFull Title (trunc)…"),
            "a.sc-product-link": FakeElement(attrs={"href": "/gp/product/B000TEST"}),
        },
        "Full Title\nFull Title (trunc)…\n₹100\nQuantity is 1",
        asin="B000TEST",
    )
    page = FakePage(items=[card])
    items = cart_tool._extract_cart_items(page)
    assert "\n" not in items[0]["title"]


def test_run_returns_setup_message_when_browser_unavailable():
    with patch.object(cart_tool.browser, "available", return_value=False):
        result = ViewCartTool().run()
    assert "isn't set up" in result


def test_run_reports_not_logged_in():
    fake_page = FakePage(logged_in=False)
    with (
        patch.object(cart_tool.browser, "available", return_value=True),
        patch.object(cart_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = ViewCartTool().run()
    assert "not logged into amazon" in result.lower()


def test_run_reports_empty_cart():
    fake_page = FakePage(items=[], logged_in=True, body_text="Your Amazon.in Cart is empty")
    with (
        patch.object(cart_tool.browser, "available", return_value=True),
        patch.object(cart_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = ViewCartTool().run()
    assert "cart is empty" in result.lower()


def test_run_returns_formatted_items_on_success():
    fake_page = FakePage(items=[make_item()], logged_in=True)
    with (
        patch.object(cart_tool.browser, "available", return_value=True),
        patch.object(cart_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = ViewCartTool().run()
    assert "Phone X" in result
    assert "₹19,999" in result
    assert "https://www.amazon.in/gp/product/B000TEST" in result
    assert "https://www.amazon.in/gp/cart/view.html" in fake_page.urls_visited[0]


def test_run_reports_unreadable_cart_when_no_items_and_not_empty():
    fake_page = FakePage(items=[], logged_in=True, body_text="something unexpected")
    with (
        patch.object(cart_tool.browser, "available", return_value=True),
        patch.object(cart_tool.browser, "get_context", return_value=FakeContext(fake_page)),
    ):
        result = ViewCartTool().run()
    assert "couldn't read the items" in result.lower()

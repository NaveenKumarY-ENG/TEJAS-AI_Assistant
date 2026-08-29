"""Amazon Tier 1.5: reads the real Amazon.in cart page and lists what's
actually in it right now (title/price/quantity/link per line item) —
read-only, same spirit as shopping_tool.py's search: it never adds, removes,
or changes anything. Reuses the same persistent, visible browser
context/login as shopping_tool.py (Tier 1) and order_tool.py (Tier 2), so
anything order_amazon added — or the user added by hand in that same browser
window — shows up here.
"""
import logging
import re
import urllib.parse

from integrations import browser
from tools.base import Tool
from tools.order_tool import _is_logged_in

logger = logging.getLogger("assistant.cart_tool")

_EMPTY_CART_MARKERS = ("your amazon.in cart is empty", "your amazon cart is empty", "your cart is empty")

# Amazon's cart line-item container has carried a data-asin attribute for
# years across markup/layout changes — same reasoning as shopping_tool.py's
# search results keying off data-component-type rather than a cosmetic CSS
# class name that changes across layout experiments far more often.
_ITEM_SELECTOR = "div.sc-list-item[data-asin]"
# Confirmed live: the SAME item container class is reused for "Saved for
# Later" items too, which are NOT actually in the cart (won't ship, won't
# be charged) — these need excluding, not the other way around. An
# earlier version of this tried to INCLUDE only "#activeCartViewForm",
# which turned out to be just ONE of potentially several active
# fulfillment groups on the same page (a real account was confirmed live
# to have separate "Amazon", "Amazon Now", "Amazon Fresh", "Amazon
# Pharmacy" groups, and — when the same product is added via a different
# seller/offer than an existing cart line for it — additional per-offer
# groups too) — an include-scope silently dropped items sitting in any
# group other than the one it happened to name. "Saved for Later" is
# reliably the one single, distinctly-IDed section, so EXCLUDING it (while
# keeping everything else the page has) is what actually generalizes.
_SAVED_FOR_LATER_CONTAINER = "#sc-saved-cart"
_QUANTITY_RE = re.compile(r"Quantity is (\d+)")


def _text(el) -> str | None:
    if not el:
        return None
    # Confirmed live: Amazon's title element renders BOTH an off-screen
    # full-text copy and a separately visible, "…"-truncated copy as
    # sibling nodes inside the same container — .inner_text() on the
    # container returns both, joined by a newline, which silently broke
    # this tool's one-line-per-item output. Collapsing all whitespace
    # (including embedded newlines) to single spaces is a general
    # safety net against that class of surprise, on top of picking a more
    # precise selector below that avoids it in the first place.
    text = " ".join(el.inner_text().split())
    return text or None


def _extract_cart_items(page) -> list[dict]:
    saved_container = page.query_selector(_SAVED_FOR_LATER_CONTAINER)
    saved_asins = set()
    if saved_container:
        for el in saved_container.query_selector_all(_ITEM_SELECTOR):
            asin = el.get_attribute("data-asin")
            if asin:
                saved_asins.add(asin)

    items = []
    for card in page.query_selector_all(_ITEM_SELECTOR):
        asin = card.get_attribute("data-asin")
        if asin and asin in saved_asins:
            continue  # actually in "Saved for Later," not the real cart
        # .a-truncate-full is Amazon's own off-screen element holding the
        # clean, complete, untruncated title text — confirmed live as
        # exactly what avoids the duplicate-text problem above.
        # .sc-product-title (the older guess) matches an ANCESTOR <a> that
        # also carries that class, whose inner_text pulls in the
        # truncated sibling too — kept only as a last-resort fallback.
        title = None
        for selector in (".a-truncate-full", "h3", ".sc-product-title"):
            text = _text(card.query_selector(selector))
            if text:
                title = text
                break
        if not title:
            continue  # not a real line item (a promo banner or layout element sharing the container class)

        # Same selector shopping_tool.py's search results already use —
        # confirmed live to work identically here.
        price = _text(card.query_selector(".a-price .a-offscreen"))

        # Confirmed live: there's no plain quantity attribute/select value
        # to read directly — Amazon's own accessible label ("Quantity is
        # 1") is the reliable source, extracted from the card's own text
        # rather than a specific nested selector that markup changes could
        # break more easily.
        quantity = None
        qty_match = _QUANTITY_RE.search(card.inner_text())
        if qty_match:
            quantity = qty_match.group(1)

        link = None
        link_el = (
            card.query_selector("a.sc-product-link")
            or card.query_selector("a[href*='/gp/product/']")
            or card.query_selector("a[href*='/dp/']")
        )
        href = link_el.get_attribute("href") if link_el else None
        if href:
            link = urllib.parse.urljoin("https://www.amazon.in", href)

        items.append({"title": title, "price": price, "quantity": quantity, "link": link})
    return items


def _format_cart(items: list[dict]) -> str:
    lines = []
    for item in items:
        parts = [item["title"]]
        if item["price"]:
            parts.append(item["price"])
        if item["quantity"]:
            parts.append(f"qty {item['quantity']}")
        line = " — ".join(parts)
        if item["link"]:
            line += f" ({item['link']})"
        lines.append(f"- {line}")
    return "\n".join(lines)


class ViewCartTool(Tool):
    name = "view_amazon_cart"
    description = (
        "Show what's actually in the user's real Amazon.in cart right now — opens the same "
        "shopping browser window used by shop_amazon/order_amazon and reads the live cart page "
        "(title/price/quantity/link per item). Read-only: never adds, removes, or changes "
        "anything in the cart."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}

    def run(self) -> str:
        if not browser.available():
            return (
                "Amazon shopping isn't set up on this server — install Playwright's browser "
                "(see README.md's Setup section) to enable this."
            )
        try:
            context = browser.get_context()
            page = context.new_page()
            page.goto("https://www.amazon.in/gp/cart/view.html", wait_until="domcontentloaded")
            page.bring_to_front()

            if not _is_logged_in(page):
                return (
                    "You're not logged into Amazon in the shopping browser window — sign in there, "
                    "then ask me again to see what's in your cart."
                )

            try:
                page.wait_for_selector(_ITEM_SELECTOR, timeout=6000)
            except Exception:
                pass

            body_text = page.inner_text("body").lower()
            if any(marker in body_text for marker in _EMPTY_CART_MARKERS):
                return "Your Amazon cart is empty."

            items = _extract_cart_items(page)
            if not items:
                return (
                    "Opened your Amazon cart, but couldn't read the items from the page (Amazon may "
                    "have changed its layout) — take a look at the browser window yourself."
                )
            return f"Here's what's in your Amazon cart:\n{_format_cart(items)}"
        except Exception as e:
            logger.exception("Reading Amazon cart failed")
            return f"Something went wrong reading your Amazon cart: {e}"

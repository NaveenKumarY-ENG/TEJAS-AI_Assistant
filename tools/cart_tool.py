"""Amazon Tier 1.5: reads the real Amazon.in cart page (ViewCartTool,
read-only) and can remove one specific item from it (RemoveFromCartTool) —
never adds or changes anything otherwise. Reuses the same persistent,
visible browser context/login as shopping_tool.py (Tier 1) and
order_tool.py (Tier 2), so anything order_amazon added — or the user added
by hand in that same browser window — shows up here.
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


def _active_cart_cards_and_items(page):
    """Yields (live_card_element, item_dict) for every item actually in the
    active cart (excludes Saved for Later — see _SAVED_FOR_LATER_CONTAINER's
    comment). Shared by _extract_cart_items (drops the live element, returns
    plain dicts — ViewCartTool's read-only use) and RemoveFromCartTool
    (needs the live element to click its delete control)."""
    saved_container = page.query_selector(_SAVED_FOR_LATER_CONTAINER)
    saved_asins = set()
    if saved_container:
        for el in saved_container.query_selector_all(_ITEM_SELECTOR):
            asin = el.get_attribute("data-asin")
            if asin:
                saved_asins.add(asin)

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

        yield card, {"title": title, "price": price, "quantity": quantity, "link": link}


def _extract_cart_items(page) -> list[dict]:
    return [item for _card, item in _active_cart_cards_and_items(page)]


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


def _click_delete_and_confirm(page, card, asin: str) -> bool:
    """Clicks the cart item's delete control and confirms the item is
    actually gone from the live page afterward — same "don't trust that a
    click didn't raise" principle as order_tool.py's _add_to_cart watching
    the cart-count badge. Even if the delete-control selector below turns
    out wrong for some layout variant, the failure mode is an honest
    "couldn't confirm it was removed," never a false success claim — the
    confirmation doesn't depend on the click having actually done anything.

    Live-verified selector: `input[value='Delete']` is the real control
    (confirmed live — a real removal genuinely succeeded). What was WRONG
    on the first live attempt was the confirmation mechanism, not the
    selector: Amazon's delete triggers a full page reload rather than an
    in-place AJAX update, and polling a JS predicate with
    wait_for_function() across that navigation raised ("execution context
    destroyed") — caught by a broad except and reported as failure even
    though the removal had already genuinely succeeded. Waiting for the
    resulting navigation to settle first, then re-querying fresh (same
    navigation-tolerant pattern order_tool.py's _go_to_checkout already
    uses for Amazon's own multi-step checkout reloads), is what actually
    tolerates it."""
    delete_el = None
    for selector in ("input[value='Delete']", "[data-action='delete'] input[type='submit']", "[data-action='delete']"):
        delete_el = card.query_selector(selector)
        if delete_el:
            break
    if not delete_el:
        return False
    delete_el.click()
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:
        pass
    # A fresh navigation, rather than querying whatever transitional state
    # the page is in right after the click+reload, sidesteps real
    # flakiness confirmed live (twice): querying immediately afterward
    # intermittently raised (a mid-transition execution context) even
    # though the removal had already genuinely succeeded both times — a
    # clean re-load guarantees a settled document before checking.
    try:
        page.goto("https://www.amazon.in/gp/cart/view.html", wait_until="domcontentloaded")
        return page.query_selector(f"div.sc-list-item[data-asin='{asin}']") is None
    except Exception:
        return False


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


_WORD_RE = re.compile(r"[a-z0-9]+")


def _matches_by_words(name: str, title: str | None) -> bool:
    """Every word of `name` appears somewhere in `title`, in any order —
    not a strict substring. Regression fix for a real bug found live: a
    strict `name in title` containment check failed on "Nataraj pen jar"
    against the real title "Nataraj GCM Ball Pen Jar" — every word IS
    genuinely present, just not contiguous (the model shortened/reordered
    the phrase asking to remove something whose exact title it had
    literally been shown moments earlier). Still conservative — this is
    AND, not OR, so "pen" alone doesn't match everything with "pen"
    somewhere in a long title — just order-independent instead of an
    exact-substring match that broke on completely ordinary phrasing."""
    if not title:
        return False
    name_words = set(_WORD_RE.findall(name.lower()))
    if not name_words:
        return False
    title_lower = title.lower()
    return all(word in title_lower for word in name_words)


class RemoveFromCartTool(Tool):
    name = "remove_from_amazon_cart"
    description = (
        "Remove one specific item from the user's real Amazon.in cart. Give EITHER "
        "product_name (must match exactly one item currently in the cart — call "
        "view_amazon_cart first if you're not sure of the exact title) OR product_url (the "
        "exact link from a view_amazon_cart result). Never removes anything if the match is "
        "ambiguous (matches more than one item) or not found — reports what's actually in the "
        "cart instead of guessing which one was meant."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "product_name": {
                "type": "string",
                "description": "The product's name/title — must match exactly one item currently in the cart. Omit if giving product_url instead.",
            },
            "product_url": {
                "type": "string",
                "description": "The exact product link, from a prior view_amazon_cart result. Omit if giving product_name instead.",
            },
        },
        "required": [],
    }

    def run(self, product_name: str = "", product_url: str = "") -> str:
        if not browser.available():
            return (
                "Amazon shopping isn't set up on this server — install Playwright's browser "
                "(see README.md's Setup section) to enable this."
            )
        product_name = (product_name or "").strip()
        product_url = (product_url or "").strip()
        if not product_name and not product_url:
            return "I need either the product's name or its exact link to know what to remove."
        try:
            context = browser.get_context()
            page = context.new_page()
            page.goto("https://www.amazon.in/gp/cart/view.html", wait_until="domcontentloaded")
            page.bring_to_front()

            if not _is_logged_in(page):
                return (
                    "You're not logged into Amazon in the shopping browser window — sign in there, "
                    "then ask me again."
                )

            try:
                page.wait_for_selector(_ITEM_SELECTOR, timeout=6000)
            except Exception:
                pass

            all_candidates = list(_active_cart_cards_and_items(page))
            if not all_candidates:
                return "Your Amazon cart is already empty — nothing to remove."

            if product_url:
                matches = [(c, i) for c, i in all_candidates if i["link"] == product_url]
            else:
                matches = [(c, i) for c, i in all_candidates if _matches_by_words(product_name, i["title"])]

            # Never guess when ambiguous — same principle as
            # order_tool.py's variant-mismatch guard (removing the wrong
            # item is a real mistake worth refusing over, not a minor
            # inconvenience).
            if len(matches) != 1:
                listing = "\n".join(
                    f"- {i['title']}" + (f" — {i['price']}" if i["price"] else "") for _c, i in all_candidates
                )
                reason = "found more than one item that could match" if len(matches) > 1 else "couldn't find a match"
                return (
                    f"I {reason} for that in your cart — here's what's actually there:\n{listing}\n"
                    "Tell me the exact title (or the link from view_amazon_cart) and I'll remove that specific one."
                )

            card, item = matches[0]
            asin = card.get_attribute("data-asin") or ""
            if not _click_delete_and_confirm(page, card, asin):
                return (
                    f"Found \"{item['title']}\" in your cart but couldn't confirm it was actually removed — "
                    "take a look at the browser window yourself."
                )
            return f"Removed \"{item['title']}\" from your cart."
        except Exception as e:
            logger.exception("Removing an Amazon cart item failed")
            return f"Something went wrong removing that from your cart: {e}"

"""Amazon Tier 2 shopping: adds a specific product to cart and reaches the
checkout REVIEW page — never completes a purchase. Reuses
integrations/browser.py's same persistent, visible profile as
tools/shopping_tool.py (Tier 1 — search only), so a login made here or during
a prior search carries over.

Completing a purchase automatically is a deliberate non-goal, not a
limitation to work around: Amazon's Terms of Service prohibit automated
purchasing, and finishing a real payment with no human confirmation is
exactly the kind of destructive/irreversible action this app's system prompt
already requires confirming first (see config.py). The real guarantee here
is structural, not just a prompt instruction — this file simply contains no
code path that clicks a final "Place your order" button, full stop.
"""
import difflib
import logging
import re

from integrations import browser
from tools import shopping_tool
from tools.base import Tool

logger = logging.getLogger("assistant.order_tool")

_LOGIN_MARKER = "sign in"


def _is_logged_in(page) -> bool:
    el = page.query_selector("#nav-link-accountList-nav-line-1")
    if not el:
        return False
    return _LOGIN_MARKER not in el.inner_text().strip().lower()


def _cart_count_text(page) -> str | None:
    el = page.query_selector("#nav-cart-count")
    return el.inner_text().strip() if el else None


def _add_to_cart(page) -> bool:
    # Confirmed live: some Amazon product pages render TWO elements sharing
    # id="add-to-cart-button" (invalid HTML, but real — a hidden duplicate
    # alongside the actual visible one, seemingly a leftover from an
    # alternate buybox layout). query_selector alone returns whichever
    # comes first in the DOM, which can be the hidden one — Playwright's
    # .click() then waits forever for an element that will never become
    # visible. Picking the first genuinely VISIBLE match reflects what the
    # user (and a real click) actually sees.
    button = None
    for candidate in page.query_selector_all("#add-to-cart-button"):
        if candidate.is_visible():
            button = candidate
            break
    if not button:
        return False
    # A successful click is not the same as the item actually landing in
    # the cart — a variant-selector popup ("choose a size/color" that
    # replaces the button before this click reaches it), a promotional
    # interstitial, or a layout change can all swallow the click silently.
    # Waiting for Amazon's own top-nav cart-count badge to actually change
    # is a real confirmation the click did something, not just that it
    # didn't raise.
    count_before = _cart_count_text(page)
    button.click()
    try:
        page.wait_for_function(
            "(before) => document.querySelector('#nav-cart-count')?.textContent?.trim() !== before",
            arg=count_before,
            timeout=6000,
        )
        return True
    except Exception:
        return False


def _go_to_checkout(page) -> bool:
    page.goto("https://www.amazon.in/gp/cart/view.html", wait_until="domcontentloaded")
    try:
        page.wait_for_selector("input[name='proceedToRetailCheckout']", timeout=8000)
    except Exception:
        return False
    page.click("input[name='proceedToRetailCheckout']")
    try:
        # Best-effort settle — the real pass/fail call is _extract_checkout_summary's
        # text check below, this just gives the page a moment to finish navigating.
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass

    # Confirmed live: Amazon sometimes inserts a "Need anything else?"
    # upsell/cross-sell carousel page ("/checkout/byg/...") between the
    # cart and the real order-review page, requiring a SECOND click on
    # "Continue to checkout" — without this, the flow silently stalled on
    # the upsell page. Confirmed live as genuinely flaky (the carousel's
    # own client-side routing occasionally needs a second attempt), so
    # this retries once rather than giving up after a single try; still a
    # no-op (nothing to retry, nothing lost) on accounts/sessions where
    # Amazon skips the interstitial entirely.
    for _ in range(2):
        if "/checkout/byg" not in page.url:
            break
        try:
            page.click("a:has-text('Continue to checkout')", timeout=6000)
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            break

    return True


_ORDER_TOTAL_RE = re.compile(r"Order Total:\s*\n?\s*(₹[\d,]+(?:\.\d+)?)")


def _extract_checkout_summary(page) -> str | None:
    """None means "couldn't confirm we're actually on a real checkout
    page" — the caller treats that as a soft failure, not a crash. Never
    returns anything that could be mistaken for a completed purchase; this
    only ever reads the page, it does not click anything on it.

    Confirmed live: Amazon's real checkout flow no longer lands on a
    "place your order" page directly after the cart — it's now a "Secure
    checkout" page (delivery address + order total + payment method
    selection) that comes BEFORE the actual "place your order" step,
    which only appears after a payment method is chosen. Advancing past
    payment-method selection would mean touching real payment
    information, exactly what this app must never do (see this file's own
    module docstring) — so "Secure checkout" is the correct, safe place
    to stop and hand off to the user, not a shortfall to push through.
    "order-summary-line-definition" (the old total selector) now
    duplicates across several summary rows (Items/Delivery/Total/Order
    Total/etc.), so the total is pulled from the page's own visible text
    instead of picking whichever row happens to be first — and finding
    that concrete "Order Total: ₹X" text IS the confirmation itself: no
    separate page-identity check needed, and nothing is claimed without a
    real number actually found on the page."""
    body = page.inner_text("body")
    match = _ORDER_TOTAL_RE.search(body)
    return f"Order total: {match.group(1)}" if match else None


def _price_change_note(page, product_url: str) -> str:
    """Amazon prices move constantly (flash deals, stock-based pricing,
    lightning deals expiring) — the price shown during an earlier
    shop_amazon search is not a guarantee of what's on the product page
    now, and the user is about to make a real cart/checkout decision
    based on it. Same product-page selector confirmed live to work
    (.a-price .a-offscreen) as the one search results already use, so the
    comparison is apples-to-apples. Returns "" (nothing to say) whenever
    either price can't be confirmed — never claims a change that wasn't
    actually verified. Must be called BEFORE the caller remembers this
    product's current price (which would overwrite the "searched" value
    this compares against)."""
    searched = shopping_tool._known_product_urls.get(product_url, {}).get("price")
    current_el = page.query_selector(".a-price .a-offscreen")
    current = current_el.inner_text().strip() if current_el else None
    searched_value = shopping_tool._parse_price(searched)
    current_value = shopping_tool._parse_price(current)
    if searched_value is None or current_value is None or abs(searched_value - current_value) < 0.01:
        return ""
    return f" Note: this was {searched} when you searched, and is now {current} on the product page — worth checking before you confirm."


def _best_match(name: str, results: list[dict]) -> dict | None:
    """Picks the closest-titled result to `name` rather than blindly
    trusting the first one — a sponsored or loosely-related listing can
    still rank first even for a highly specific query. Falls back to the
    first result if nothing scores confidently, since Amazon's own
    relevance ranking for a long, specific title (the common case here —
    the user reading out or pasting a full product name/spec dump) is
    still a reasonable best-effort guess rather than nothing at all."""
    if not results:
        return None
    name_lower = name.strip().lower()
    scored = sorted(
        results,
        key=lambda r: difflib.SequenceMatcher(None, name_lower, (r["title"] or "").lower()).ratio(),
        reverse=True,
    )
    best = scored[0]
    ratio = difflib.SequenceMatcher(None, name_lower, (best["title"] or "").lower()).ratio()
    return best if ratio >= 0.3 else results[0]


class OrderAmazonTool(Tool):
    name = "order_amazon"
    description = (
        "Add a specific Amazon product to cart and reach the checkout REVIEW page. Give EITHER "
        "product_url (an exact product link — from a prior shop_amazon result, or a real "
        "Amazon.in/amzn.in link the user shared directly) OR product_name (the product's full "
        "name/title, when the user described or read out the product instead of giving a link — "
        "this searches Amazon for it and orders the closest-matching result). Never completes "
        "the purchase: stops at checkout review and tells the user to click 'Place your order' "
        "themselves. Requires the user to already be logged into Amazon in the shopping browser "
        "window (the same one shop_amazon opens) — if not, this reports that clearly instead of "
        "trying to log in on their behalf."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "product_url": {
                "type": "string",
                "description": "The exact Amazon product URL — from a prior shop_amazon result, or a real Amazon.in/amzn.in link the user shared directly. Omit if you're giving product_name instead.",
            },
            "product_name": {
                "type": "string",
                "description": "The product's full name/title, when there's no link — e.g. the user typed or spoke the product name instead of pasting a URL. Omit if you're giving product_url instead.",
            },
        },
        "required": [],
    }

    def run(self, product_url: str = "", product_name: str = "") -> str:
        if not browser.available():
            return (
                "Amazon shopping isn't set up on this server — install Playwright's browser "
                "(see README.md's Setup section) to enable this."
            )
        product_url = (product_url or "").strip()
        product_name = (product_name or "").strip()
        if not product_url and not product_name:
            return (
                "I need either a product link or the product's name to order it — search with "
                "shop_amazon first if you don't have a link handy."
            )

        # A link the user shares directly is trusted differently than one
        # the model would otherwise have to recall/reproduce from memory —
        # see shopping_tool.looks_like_amazon_url's own docstring. Checked
        # up front, before opening any browser page, so an obviously wrong
        # link (not Amazon at all) never even gets navigated to.
        if product_url and not (
            shopping_tool.is_known_product_url(product_url) or shopping_tool.looks_like_amazon_url(product_url)
        ):
            return (
                "That doesn't look like a real Amazon.in product link, so I won't act on it — "
                "share the actual product page link, or search for it with shop_amazon first."
            )

        try:
            if not product_url:
                # Resolve a plain product name/description to a real URL via
                # a live search — the exact same real browser code path
                # shop_amazon itself uses (search_products), not a guess.
                outcome = shopping_tool.search_products(product_name)
                if outcome.get("captcha"):
                    return (
                        f"Amazon showed a verification challenge while looking up '{product_name}' — "
                        "the browser window is open for you to check it yourself."
                    )
                results = outcome.get("results") or []
                if not results:
                    return (
                        f"Couldn't find '{product_name}' on Amazon — try shop_amazon with a shorter "
                        "or slightly different search first."
                    )
                match = _best_match(product_name, results)
                product_url = (match or {}).get("link")
                if not product_url:
                    return (
                        f"Found a likely match for '{product_name}' but couldn't get its link — try "
                        "shop_amazon directly and order using the link from those results."
                    )

            context = browser.get_context()
            page = context.new_page()
            page.goto(product_url, wait_until="domcontentloaded")
            page.bring_to_front()

            # Confirms this is genuinely a product page — the real gate for
            # a pasted/shared link and a name-resolved link alike (a short
            # link that redirected somewhere unexpected, a stale/removed
            # listing, a hallucinated-but-shape-valid URL that 404s). Also
            # doubles as the source of the product's real title for the
            # success message below.
            title_el = page.query_selector("#productTitle")
            if not title_el:
                return (
                    "Opened that link, but it doesn't look like a real Amazon product page — the "
                    "browser window is open for you to check it yourself."
                )
            product_title = title_el.inner_text().strip()

            price_el = page.query_selector(".a-price .a-offscreen")
            current_price = price_el.inner_text().strip() if price_el else None

            # Must run before _remember_results below overwrites the cached
            # "searched" price with this current one.
            price_note = _price_change_note(page, product_url)
            shopping_tool._remember_results(
                [{"title": product_title, "price": current_price, "rating": None, "link": product_url}]
            )

            if not _is_logged_in(page):
                return (
                    "You're not logged into Amazon in the shopping browser window — sign in there "
                    "(the product page is already open for you), then ask me to try again."
                )

            if not _add_to_cart(page):
                return (
                    "Couldn't confirm the item was actually added to your cart — either there's no "
                    "'Add to Cart' button on that page (a listing with no single default seller, or "
                    "Amazon's layout changed), or the click didn't go through (a size/color prompt, "
                    "a promo popup). Take a look at the browser window yourself."
                )

            price_suffix = f" ({current_price})" if current_price else ""
            added_line = f'Added "{product_title}"{price_suffix} to your cart: {product_url}'

            if not _go_to_checkout(page):
                return (
                    f"{added_line}\nCouldn't reach checkout automatically — the browser window is "
                    "open at your cart for you to continue there."
                )

            summary = _extract_checkout_summary(page)
            if summary is None:
                return (
                    f"{added_line}\nNavigated toward checkout, but couldn't confirm a real checkout "
                    "page loaded correctly — check the browser window before proceeding."
                )
            return (
                f"{added_line}\nReached Amazon's checkout review page. {summary}{price_note} Review "
                "the details in the browser window, choose a payment method, and click 'Place your "
                "order' yourself when ready — I never complete a purchase or select a payment method "
                "automatically."
            )
        except Exception as e:
            logger.exception("Amazon order flow failed")
            return f"Something went wrong ordering from Amazon: {e}"

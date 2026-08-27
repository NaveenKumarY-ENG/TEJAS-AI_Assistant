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
import logging

from integrations import browser
from tools.base import Tool

logger = logging.getLogger("assistant.order_tool")

_LOGIN_MARKER = "sign in"


def _is_logged_in(page) -> bool:
    el = page.query_selector("#nav-link-accountList-nav-line-1")
    if not el:
        return False
    return _LOGIN_MARKER not in el.inner_text().strip().lower()


def _add_to_cart(page) -> bool:
    button = page.query_selector("#add-to-cart-button")
    if not button:
        return False
    button.click()
    return True


def _go_to_checkout(page) -> bool:
    page.goto("https://www.amazon.com/gp/cart/view.html", wait_until="domcontentloaded")
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
    return True


def _extract_checkout_summary(page) -> str | None:
    """None means "couldn't confirm we're actually on the review page" — the
    caller treats that as a soft failure, not a crash. Never returns
    anything that could be mistaken for a completed purchase; this only
    ever reads the page, it does not click anything on it."""
    body = page.inner_text("body")
    if "place your order" not in body.lower():
        return None
    total_el = page.query_selector("#subtotals-marketplace-table, .order-summary-line-definition")
    return total_el.inner_text().strip() if total_el else "Reached checkout review."


class OrderAmazonTool(Tool):
    name = "order_amazon"
    description = (
        "Add a specific Amazon product to cart and reach the checkout REVIEW page. "
        "Requires a product_url from a prior shop_amazon search result already in this "
        "conversation — call shop_amazon first if you don't have one. Never completes "
        "the purchase: stops at checkout review and tells the user to click 'Place your "
        "order' themselves. Requires the user to already be logged into Amazon in the "
        "shopping browser window (the same one shop_amazon opens) — if not, this reports "
        "that clearly instead of trying to log in on their behalf."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "product_url": {"type": "string", "description": "The exact Amazon product URL to order"}
        },
        "required": ["product_url"],
    }

    def run(self, product_url: str) -> str:
        if not browser.available():
            return (
                "Amazon shopping isn't set up on this server — install Playwright's browser "
                "(see README.md's Setup section) to enable this."
            )
        try:
            context = browser.get_context()
            page = context.new_page()
            page.goto(product_url, wait_until="domcontentloaded")
            page.bring_to_front()

            if not _is_logged_in(page):
                return (
                    "You're not logged into Amazon in the shopping browser window — sign in there "
                    "(the product page is already open for you), then ask me to try again."
                )

            if not _add_to_cart(page):
                return (
                    "Couldn't find an 'Add to Cart' button on that page — it may be a listing with "
                    "no single default seller, or Amazon's layout changed. Take a look at the "
                    "browser window yourself."
                )

            if not _go_to_checkout(page):
                return (
                    "Added the item to your cart, but couldn't reach checkout automatically — "
                    "the browser window is open at your cart for you to continue there."
                )

            summary = _extract_checkout_summary(page)
            if summary is None:
                return (
                    "Added the item to your cart and navigated toward checkout, but couldn't confirm "
                    "the review page loaded correctly — check the browser window before proceeding."
                )
            return (
                f"Added to your cart and reached the checkout review page. {summary} "
                "Review the details in the browser window and click 'Place your order' yourself "
                "when ready — I never complete a purchase automatically."
            )
        except Exception as e:
            logger.exception("Amazon order flow failed")
            return f"Something went wrong ordering from Amazon: {e}"

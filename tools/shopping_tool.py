"""Amazon product search via a real browser (integrations/browser.py) —
Tier 1 shopping: search, show real results in chat, and leave a real,
visible browser window open on the search for the user to browse further
themselves. Deliberately does NOT add to cart or attempt checkout/payment —
see integrations/browser.py's docstring and this project's plan for why
that's a separate, not-yet-built tier (Amazon's bot-detection/ToS and the
financial risk of an unsupervised purchase are real, not just caution for
its own sake).
"""
import logging
import urllib.parse

from integrations import browser
from tools.base import Tool

logger = logging.getLogger("assistant.shopping_tool")

_MAX_RESULTS = 5

# Amazon's own wording for its bot-verification challenge page — if this
# shows up, extraction below will find zero real results no matter what the
# selectors are, and reporting that honestly (rather than a bare "no
# results") tells the user there's a real page open for them to solve it if
# they want, not that the search itself came up empty.
_CAPTCHA_MARKERS = ("Enter the characters you see below", "Type the characters you see in this image")


def _extract_results(page) -> list[dict]:
    results = []
    # data-component-type="s-search-result" is the attribute Amazon's own
    # search results have carried for years specifically per-listing —
    # more stable than any of its cosmetic CSS class names, which change
    # across layout experiments far more often.
    cards = page.query_selector_all('[data-component-type="s-search-result"]')
    for card in cards[:_MAX_RESULTS]:
        title = None
        for selector in ("h2 span", "h2 a span", "h2"):
            el = card.query_selector(selector)
            if el:
                text = el.inner_text().strip()
                if text:
                    title = text
                    break
        if not title:
            continue  # a sponsored/layout card with no real title isn't a useful result

        price = None
        price_el = card.query_selector(".a-price .a-offscreen")
        if price_el:
            price = price_el.inner_text().strip()

        rating = None
        rating_el = card.query_selector(".a-icon-alt")
        if rating_el:
            rating = rating_el.inner_text().strip()

        link = None
        # h2 a first (older/some layouts nest the link inside the heading),
        # then a.a-link-normal — confirmed live as the actual current
        # structure: Amazon's h2 is now a standalone element (just a <span>
        # inside), with the real product link as a SEPARATE sibling <a
        # class="a-link-normal">, not a descendant of h2 at all. Without this
        # fallback, link came back None for every single result — a silent
        # failure that would have structurally broken order_amazon (Tier 2),
        # which requires a real product_url extracted from here.
        link_el = card.query_selector("h2 a") or card.query_selector("a.a-link-normal")
        href = link_el.get_attribute("href") if link_el else None
        if href:
            link = urllib.parse.urljoin("https://www.amazon.com", href)

        results.append({"title": title, "price": price, "rating": rating, "link": link})
    return results


def _format_results(results: list[dict]) -> str:
    lines = []
    for r in results:
        parts = [r["title"]]
        if r["price"]:
            parts.append(r["price"])
        if r["rating"]:
            parts.append(r["rating"])
        line = " — ".join(parts)
        if r["link"]:
            line += f" ({r['link']})"
        lines.append(f"- {line}")
    return "\n".join(lines)


class ShopAmazonTool(Tool):
    name = "shop_amazon"
    description = (
        "Search Amazon for a product and show real results. Opens a real, visible "
        "browser window navigated to the search so the user can browse further "
        "themselves — does not add to cart or purchase anything."
    )
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What to search for, e.g. 'phones'"}},
        "required": ["query"],
    }

    def run(self, query: str) -> str:
        if not browser.available():
            return (
                "Amazon shopping isn't set up on this server — install Playwright's browser "
                "(see README.md's Setup section) to enable this."
            )
        try:
            context = browser.get_context()
            page = context.new_page()
            url = f"https://www.amazon.com/s?k={urllib.parse.quote(query)}"
            page.goto(url, wait_until="domcontentloaded")
            page.bring_to_front()

            # Amazon renders its actual result cards client-side after the
            # initial HTML lands — domcontentloaded alone fires too early to
            # find them (confirmed live). Waiting for the real selector (with
            # a bounded timeout, not a blind sleep) is what's actually being
            # waited for; a timeout here just means genuinely zero results,
            # a CAPTCHA page, or a layout change — the checks below sort out which.
            try:
                page.wait_for_selector('[data-component-type="s-search-result"]', timeout=8000)
            except Exception:
                pass

            body_text = page.inner_text("body")
            if any(marker in body_text for marker in _CAPTCHA_MARKERS):
                return (
                    f"Amazon showed a verification challenge for '{query}' — the browser window "
                    "is open for you to check it yourself."
                )

            results = _extract_results(page)
            if not results:
                return (
                    f"Opened Amazon's search for '{query}' in a browser window, but couldn't read "
                    "any listings from the page (Amazon may have changed its layout) — take a look "
                    "at the window yourself."
                )
            return f"Opened Amazon and found these for '{query}':\n{_format_results(results)}"
        except Exception as e:
            logger.exception("Amazon shopping search failed")
            return f"Something went wrong searching Amazon: {e}"

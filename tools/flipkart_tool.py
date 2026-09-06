"""Flipkart product search via the shared browser (integrations/browser.py)
— research/browse only, modeled directly on tools/shopping_tool.py's
ShopAmazonTool (Tier 1: Amazon search). Deliberately does NOT attempt
cart/checkout automation the way tools/order_tool.py does for Amazon:
there is no existing Flipkart integration or anti-bot tuning in this
codebase to build on (unlike Amazon, whose browser context is specifically
tuned — spoofed UA, navigator.webdriver masking — against bot detection
confirmed live), so pretending to add-to-cart/order here would risk
claiming an action that didn't really happen. If the user wants to buy
something on Flipkart, this tool (and config.py's system prompt) is
explicit that the model should say so plainly and offer to search/open
the site instead, never fake a purchase step.
"""
import logging
import re
import urllib.parse

from config import config
from integrations import browser
from tools.base import Tool

logger = logging.getLogger("assistant.flipkart_tool")

_MAX_RESULTS = 10
_RUPEE_RE = re.compile(r"₹\s?[\d,]+")
# Two shapes confirmed live on real Flipkart search-result cards: Amazon-style
# "4.3 out of 5"/"4.3★", and Flipkart's own actual rendering, a rating glued
# directly onto its rating-count with no separator — "4.210,069 Ratings & 719
# Reviews" (that's "4.2" then "10,069" then " Ratings", no space between the
# rating and the count).
_RATING_RE = re.compile(r"\b([1-5](?:\.\d)?)\b(?=\s*(?:★|out of|stars))|\b([1-5]\.\d)\d*[\d,]*\s*Ratings")


def _parse_price(price_text: str | None) -> float | None:
    """'₹1,490' -> 1490.0. None if there's no parseable number — a result
    with an unreadable price must not be claimed to comply with a stated
    budget just because it wasn't proven NOT to (same rule
    shopping_tool.py's _parse_price applies for Amazon)."""
    if not price_text:
        return None
    cleaned = re.sub(r"[^\d.]", "", price_text)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _price_bound_description(min_price: float | None, max_price: float | None) -> str:
    if min_price is not None and max_price is not None:
        return f"between ₹{min_price:g} and ₹{max_price:g}"
    if max_price is not None:
        return f"under ₹{max_price:g}"
    return f"over ₹{min_price:g}"


def _filter_by_price(results: list[dict], min_price: float | None, max_price: float | None) -> list[dict]:
    """Client-side only — unlike shopping_tool.py's Amazon filter, there's
    no confirmed Flipkart URL price-filter parameter to build on here
    (Amazon's rh=p_36 was confirmed directly against Amazon's own generated
    filter links; nothing equivalent has been verified for Flipkart in this
    codebase), so this doesn't attempt to fabricate one — it just drops
    anything that can't be confirmed in range from the extracted results."""
    if min_price is None and max_price is None:
        return results
    kept = []
    for r in results:
        price = _parse_price(r["price"])
        if price is None:
            continue
        if min_price is not None and price < min_price:
            continue
        if max_price is not None and price > max_price:
            continue
        kept.append(r)
    return kept


def _extract_results(page) -> list[dict]:
    """Flipkart's search result cards don't carry a stable, semantic
    data-* attribute the way Amazon's data-component-type does — its CSS
    class names are short, obfuscated, and change across deploys. The one
    structurally stable thing about a Flipkart product card is that its
    link always points at a URL containing "/p/" (the product permalink
    segment, per Flipkart's own published URL scheme:
    flipkart.com/<slug>/p/<id>...), so extraction anchors on that instead
    of a specific class name.

    NOTE: unlike the original version of this function, title/price/rating
    extraction below WAS confirmed live (a real search for "samsung phone"
    against the live site) — findings folded in directly: every card's
    <img alt="..."> inside the product link holds the exact clean product
    name (confirmed on 6/6 sampled cards; the anchor's own innerText also
    "works" but is polluted with "Bestseller"/"Add to Compare" noise ahead
    of the real title), so that's preferred over the anchor's own text.
    run() below still reports "no_listings" honestly rather than silently
    returning nothing meaningful if Flipkart's markup shifts again in the
    future, the same honesty contract shopping_tool.py's Amazon path
    already follows."""
    results = []
    seen_hrefs: set[str] = set()
    anchors = page.query_selector_all('a[href*="/p/"]')
    for a in anchors:
        if len(results) >= _MAX_RESULTS:
            break
        href = a.get_attribute("href")
        if not href or href in seen_hrefs:
            continue

        img_alt = None
        try:
            img_alt = a.evaluate("el => { const img = el.querySelector('img'); return img ? img.getAttribute('alt') : null; }")
        except Exception:
            pass
        title = (img_alt or a.get_attribute("title") or a.inner_text() or "").strip()
        if not title:
            continue
        seen_hrefs.add(href)

        # Price/rating live in sibling/ancestor elements, not inside the
        # link itself — climb a few parent levels and pull a rupee-amount /
        # rating pattern out of the surrounding text, without guessing at
        # any specific (obfuscated, unconfirmed) class name.
        try:
            container_text = a.evaluate(
                "el => { let c = el; for (let i = 0; i < 3 && c.parentElement; i++) c = c.parentElement; "
                "return c.innerText || ''; }"
            )
        except Exception:
            container_text = ""

        price_match = _RUPEE_RE.search(container_text)
        rating_match = _RATING_RE.search(container_text)
        rating = None
        if rating_match:
            rating = rating_match.group(1) or rating_match.group(2)
        link = urllib.parse.urljoin("https://www.flipkart.com", href)
        results.append(
            {
                "title": title,
                "price": price_match.group(0) if price_match else None,
                "rating": rating,
                "link": link,
            }
        )
    return results


def search_products(query: str, min_price: float | None = None, max_price: float | None = None) -> dict:
    """Runs a real Flipkart search for `query` in the shared browser and
    returns a dict describing the outcome — same shape as
    shopping_tool.py's search_products for Amazon:
      - {"results": [...]} on success (possibly empty if a price filter
        matched nothing)
      - {"no_listings": True} if nothing could be extracted from the page
    Raises on a genuine navigation/setup failure, left to the caller to
    catch, same as every other browser call in this codebase."""
    context = browser.get_context()
    page = context.new_page()
    page.goto(f"https://www.flipkart.com/search?q={urllib.parse.quote(query)}", wait_until="domcontentloaded")
    page.bring_to_front()

    try:
        page.wait_for_selector('a[href*="/p/"]', timeout=8000)
    except Exception:
        pass

    results = _extract_results(page)
    if not results:
        return {"no_listings": True}

    if min_price is not None or max_price is not None:
        results = _filter_by_price(results, min_price, max_price)

    return {"results": results}


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


class ShopFlipkartTool(Tool):
    name = "shop_flipkart"
    description = (
        "Search Flipkart for a product and show real results, or just open Flipkart with no "
        "particular product in mind (omit query for 'open Flipkart'). Opens a real, visible "
        "browser window. RESEARCH ONLY — unlike shop_amazon, this cannot add anything to a cart "
        "or place an order; if the user wants to buy something on Flipkart, say so plainly and "
        "offer to open the product page instead. If the user gives a price constraint ('under "
        "2000', 'between 500 and 1500'), put the plain product name in query and the number(s) in "
        "min_price/max_price."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for, e.g. 'wireless earbuds' — the product only, no price phrase. Omit or leave empty to just open Flipkart's homepage.",
            },
            "min_price": {
                "type": "number",
                "description": "Lowest acceptable price in rupees. Omit if there's no lower bound.",
            },
            "max_price": {
                "type": "number",
                "description": "Highest acceptable price in rupees. Omit if there's no upper bound.",
            },
        },
        "required": [],
    }

    def run(self, query: str = "", min_price: float | None = None, max_price: float | None = None) -> str:
        if not config.live_ai_enabled:
            return "Live.AI (real-time Flipkart research) is disabled on this server."
        if not browser.available():
            return (
                "Browser automation isn't set up on this server — install Playwright's browser "
                "(see README.md's Setup section) to enable this."
            )
        query = (query or "").strip()
        if min_price is not None and max_price is not None and min_price > max_price:
            return f"That price range doesn't make sense (₹{min_price:g} to ₹{max_price:g}) — the minimum is higher than the maximum."
        try:
            if not query:
                context = browser.get_context()
                page = context.new_page()
                page.goto("https://www.flipkart.com", wait_until="domcontentloaded")
                page.bring_to_front()
                return "Opened Flipkart for you — browse in the window, or tell me what to search for."

            outcome = search_products(query, min_price, max_price)

            if outcome.get("no_listings"):
                return (
                    f"Opened Flipkart's search for '{query}' in a browser window, but couldn't read "
                    "any listings from the page (Flipkart may have changed its layout, or shown a "
                    "login prompt) — take a look at the window yourself."
                )

            results = outcome["results"]
            has_price_filter = min_price is not None or max_price is not None
            if has_price_filter and not results:
                return (
                    f"Opened Flipkart's search for '{query}' — found results, but none confirmed "
                    f"{_price_bound_description(min_price, max_price)}. Take a look at the browser "
                    "window yourself, or try a wider range."
                )

            suffix = f" {_price_bound_description(min_price, max_price)}" if has_price_filter else ""
            return (
                f"Opened Flipkart and found these for '{query}'{suffix} (research only — I can't "
                f"add these to a cart or order them):\n{_format_results(results)}"
            )
        except Exception as e:
            logger.exception("Flipkart shopping search failed")
            return f"Something went wrong searching Flipkart: {e}"

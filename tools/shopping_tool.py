"""Amazon product search via a real browser (integrations/browser.py) —
Tier 1 shopping: search, show real results in chat, and leave a real,
visible browser window open on the search for the user to browse further
themselves. Does NOT add to cart or attempt checkout/payment itself — see
tools/order_tool.py (Tier 2) for that, which reuses the same browser
context/login state.
"""
import logging
import re
import urllib.parse
from collections import OrderedDict

from integrations import browser
from tools.base import Tool

logger = logging.getLogger("assistant.shopping_tool")

# 10, not 5 — "show me the best phones" reads as a thin, unhelpful answer
# with only 5 options; Amazon's own search results page always renders
# well more than 10 real cards per page, so this doesn't risk running out.
_MAX_RESULTS = 10

# Real product URLs seen in an actual shop_amazon result, so order_tool.py
# can verify a product_url it's asked to order was genuinely returned by a
# search rather than trusted at face value. A local model reproducing a
# long URL from earlier in the conversation verbatim is exactly the kind of
# thing confirmed unreliable elsewhere in this codebase (garbled filenames,
# altered table values) — here, unlike those, a garbled/hallucinated value
# would mean silently trying to buy the wrong item, so it gets a hard check
# instead of just hoping the model got it right. Global, not per-session:
# integrations/browser.py's browser context is itself a single shared
# singleton for the whole process (one shopping window for the one real
# user this app is built for), so scoping this any tighter would need
# session-id plumbing that doesn't exist anywhere in the tool-call path
# (tools/base.py's Tool.run() only ever receives the model's own arguments)
# for a mismatch this app's actual usage pattern doesn't create. Bounded by
# count (not time) — simplest way to avoid unbounded growth over a
# long-running server without needing an arbitrary staleness cutoff; an
# order attempt against a URL is always resolved against Amazon's real,
# current page regardless of how long ago the search happened, so keeping
# old entries has no correctness cost, only a memory one.
_MAX_KNOWN_URLS = 200
_known_product_urls: "OrderedDict[str, dict]" = OrderedDict()


def is_known_product_url(url: str) -> bool:
    """Whether `url` was returned by a real shop_amazon search — see
    _known_product_urls' comment. Used by order_tool.py before acting on a
    product_url it's given."""
    return url in _known_product_urls


# Amazon's own share links (what the Amazon app generates when you tap
# "Share" on a product) redirect through these short-link domains rather
# than a full amazon.in/dp/... URL, so a URL shape check alone can't handle
# them — Playwright follows the redirect fine, this just needs to know
# they're trustworthy enough to navigate to in the first place.
_SHORT_LINK_HOSTS = {"amzn.in", "www.amzn.in", "amzn.to", "www.amzn.to"}
_FULL_LINK_HOSTS = {"amazon.in", "www.amazon.in"}
# A real Amazon product path always contains /dp/<10-char ASIN> or
# /gp/product/<10-char ASIN> somewhere in it (an optional title slug can
# come first) — confirmed against every product URL extracted live by
# _extract_results below.
_PRODUCT_PATH_RE = re.compile(r"/(?:dp|gp/product)/[A-Za-z0-9]{10}(?:[/?]|$)")


def looks_like_amazon_url(url: str) -> bool:
    """A link the user shares directly (pasted into chat, or spoken and
    transcribed) is trusted differently than a product_url the model would
    otherwise have to recall/reproduce from memory — see
    _known_product_urls' comment on why THAT can't be trusted at face
    value. This only accepts a real Amazon.in domain, and — for the full
    domain, where the shape can actually be checked — something that looks
    like a genuine product path, not just any amazon.in URL (a bare
    homepage or search URL doesn't pass). Short-link domains are opaque by
    design, so they're accepted on domain alone here.

    This is a cheap first filter, not the only safeguard: order_tool.py
    still requires, after actually navigating there, that a real product
    page loaded (#productTitle present) before doing anything else — so
    even a syntactically valid but hallucinated/nonexistent link gets
    caught live, not just pattern-matched."""
    try:
        parsed = urllib.parse.urlparse((url or "").strip())
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    if parsed.hostname in _SHORT_LINK_HOSTS:
        return True
    if parsed.hostname not in _FULL_LINK_HOSTS:
        return False
    if _PRODUCT_PATH_RE.search(parsed.path):
        return True
    # Amazon's own sponsored-listing click-through wrapper —
    # /sspa/click?...&url=<url-encoded real product path>... — confirmed
    # live as a common, completely legitimate link shape (2-3 of every 5
    # search results are routinely sponsored, and _extract_results above
    # captures exactly this URL as their `link`), not a rare edge case. The
    # real product path is inside the `url` query parameter, not the
    # visible path, so it needs decoding rather than a plain path match.
    if parsed.path.startswith("/sspa/click"):
        inner = urllib.parse.parse_qs(parsed.query).get("url", [None])[0]
        if inner and _PRODUCT_PATH_RE.search(inner):
            return True
    return False


def _remember_results(results: list[dict]) -> None:
    for r in results:
        if not r["link"]:
            continue
        _known_product_urls[r["link"]] = r
        _known_product_urls.move_to_end(r["link"])
    while len(_known_product_urls) > _MAX_KNOWN_URLS:
        _known_product_urls.popitem(last=False)

# Amazon's own wording for its bot-verification challenge page — if this
# shows up, extraction below will find zero real results no matter what the
# selectors are, and reporting that honestly (rather than a bare "no
# results") tells the user there's a real page open for them to solve it if
# they want, not that the search itself came up empty.
_CAPTCHA_MARKERS = ("Enter the characters you see below", "Type the characters you see in this image")


def _parse_price(price_text: str | None) -> float | None:
    """'₹1,490' -> 1490.0. None if there's no parseable number — a result
    with an unreadable price is exactly the case _filter_by_price below
    must not claim complies with a stated budget just because it wasn't
    proven NOT to."""
    if not price_text:
        return None
    cleaned = re.sub(r"[^\d.]", "", price_text)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _price_range_param(min_price: float | None, max_price: float | None) -> str | None:
    """Amazon's real price-filter URL parameter — confirmed live directly
    from Amazon.in's own generated filter-sidebar links (not guessed):
    "Up to ₹350" -> rh=p_36:-35000, "₹350 - ₹600" -> p_36:35000-60000,
    "Over ₹1,800" -> p_36:180000-. Units are paise (rupees * 100); either
    bound can be blank for an open-ended range. None if no bound was given
    at all, so run() can skip adding the parameter entirely."""
    if min_price is None and max_price is None:
        return None
    lo = str(round(min_price * 100)) if min_price is not None else ""
    hi = str(round(max_price * 100)) if max_price is not None else ""
    return f"p_36:{lo}-{hi}"


def _price_bound_description(min_price: float | None, max_price: float | None) -> str:
    if min_price is not None and max_price is not None:
        return f"between ₹{min_price:g} and ₹{max_price:g}"
    if max_price is not None:
        return f"under ₹{max_price:g}"
    return f"over ₹{min_price:g}"


def _filter_by_price(results: list[dict], min_price: float | None, max_price: float | None) -> list[dict]:
    """Amazon's own rh=p_36 URL filter (applied in run() below) narrows the
    search server-side, but confirmed live as NOT a hard guarantee — a
    plain free-text "under 1000" query returned results up to ₹1,490, and
    even the real p_36 filter parameter is worth double-checking rather
    than trusted blindly (sponsored placements are exactly the kind of
    listing that can end up ranked outside a filter's real boundary). A
    result whose price can't be confirmed in range (unparseable, or
    genuinely missing) is dropped rather than shown as if it qualified —
    same "don't claim what wasn't verified" principle already applied to
    OCR extraction and Whisper transcription confidence elsewhere in this
    codebase."""
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
    results = []
    # data-component-type="s-search-result" is the attribute Amazon's own
    # search results have carried for years specifically per-listing —
    # more stable than any of its cosmetic CSS class names, which change
    # across layout experiments far more often.
    cards = page.query_selector_all('[data-component-type="s-search-result"]')
    for card in cards[:_MAX_RESULTS]:
        # Confirmed live: Amazon's current markup has TWO <h2> elements per
        # card — a brand-only mini heading first ("OnePlus"), then the real
        # full title second, marked with aria-label (and duplicated as the
        # text of a <span> inside it). A plain "h2 span" selector matches
        # the FIRST one in document order — the brand-only heading — and
        # every result silently came back titled just "OnePlus" instead of
        # the actual product name, a real regression this fixes by
        # preferring the aria-label'd h2 specifically, not just any h2.
        title = None
        aria_h2 = card.query_selector("h2[aria-label]")
        if aria_h2:
            text = (aria_h2.get_attribute("aria-label") or "").strip()
            if text:
                title = text
        if not title:
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
            link = urllib.parse.urljoin("https://www.amazon.in", href)

        results.append({"title": title, "price": price, "rating": rating, "link": link})
    return results


def search_products(query: str, min_price: float | None = None, max_price: float | None = None) -> dict:
    """Runs a real Amazon.in search for `query` in the shared shopping
    browser and returns a dict describing the outcome:
      - {"results": [...]} on success (possibly an empty list if a price
        filter matched nothing)
      - {"captcha": True} if Amazon showed its bot-verification challenge
      - {"no_listings": True} if nothing could be extracted from the page
    Remembers any returned results the same way ShopAmazonTool.run() always
    has (see _remember_results). This is the one real code path for
    "search Amazon and get results back" — ShopAmazonTool.run() (Tier 1,
    the user-facing tool) and order_tool.py's product-name resolution
    (Tier 2, "order the iQOO Z11" with no link) both go through this exact
    function rather than duplicating the browser/extraction logic, or
    order_tool.py reimplementing its own weaker version of it. Raises on a
    genuine navigation/setup failure — left to the caller to catch, same as
    any other browser call in this codebase."""
    context = browser.get_context()
    page = context.new_page()
    url = f"https://www.amazon.in/s?k={urllib.parse.quote(query)}"
    price_param = _price_range_param(min_price, max_price)
    if price_param:
        url += f"&rh={urllib.parse.quote(price_param)}"
    page.goto(url, wait_until="domcontentloaded")
    page.bring_to_front()

    try:
        page.wait_for_selector('[data-component-type="s-search-result"]', timeout=8000)
    except Exception:
        pass

    body_text = page.inner_text("body")
    if any(marker in body_text for marker in _CAPTCHA_MARKERS):
        return {"captcha": True}

    results = _extract_results(page)
    if not results:
        return {"no_listings": True}

    if min_price is not None or max_price is not None:
        results = _filter_by_price(results, min_price, max_price)

    _remember_results(results)
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


class ShopAmazonTool(Tool):
    name = "shop_amazon"
    description = (
        "Search Amazon for a product and show real results, or just open Amazon with no "
        "particular product in mind (omit query, or leave it empty, for 'open Amazon'/'open "
        "amazon.in' with nothing to search for yet). Opens a real, visible browser window — "
        "does not add to cart or purchase anything. If the user gives a price constraint "
        "('under 1000', 'between 500 and 1500', 'over 2000'), put the plain product name in "
        "query and the number(s) in min_price/max_price — do NOT leave the price phrase in "
        "query itself, a real filter is applied and double-checked against min_price/max_price, "
        "which is more reliable than Amazon's own free-text search interpretation."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for, e.g. 'phones' — the product only, no price phrase. Omit or leave empty to just open Amazon's homepage.",
            },
            "min_price": {
                "type": "number",
                "description": "Lowest acceptable price in rupees, e.g. 500 for 'over 500' or 'between 500 and 1500'. Omit if there's no lower bound.",
            },
            "max_price": {
                "type": "number",
                "description": "Highest acceptable price in rupees, e.g. 1000 for 'under 1000' or 'between 500 and 1500'. Omit if there's no upper bound.",
            },
        },
        "required": [],
    }

    def run(self, query: str = "", min_price: float | None = None, max_price: float | None = None) -> str:
        if not browser.available():
            return (
                "Amazon shopping isn't set up on this server — install Playwright's browser "
                "(see README.md's Setup section) to enable this."
            )
        query = (query or "").strip()
        if min_price is not None and max_price is not None and min_price > max_price:
            return f"That price range doesn't make sense (₹{min_price:g} to ₹{max_price:g}) — the minimum is higher than the maximum."
        try:
            if not query:
                # "Open Amazon" with nothing to search for yet — the plain
                # homepage, not a search with an empty term (confirmed live
                # as a real, confusing gap: amazon.in/s?k= with no query
                # renders no result cards at all, which the old code then
                # reported as "couldn't read any listings ... Amazon may
                # have changed its layout" — a misleading error for a case
                # where nothing was actually wrong, the user just wanted to
                # browse rather than search).
                context = browser.get_context()
                page = context.new_page()
                page.goto("https://www.amazon.in", wait_until="domcontentloaded")
                page.bring_to_front()
                return "Opened Amazon.in for you — browse in the window, or tell me what to search for."

            outcome = search_products(query, min_price, max_price)

            if outcome.get("captcha"):
                return (
                    f"Amazon showed a verification challenge for '{query}' — the browser window "
                    "is open for you to check it yourself."
                )

            if outcome.get("no_listings"):
                return (
                    f"Opened Amazon's search for '{query}' in a browser window, but couldn't read "
                    "any listings from the page (Amazon may have changed its layout) — take a look "
                    "at the window yourself."
                )

            results = outcome["results"]
            has_price_filter = min_price is not None or max_price is not None
            if has_price_filter and not results:
                return (
                    f"Opened Amazon's search for '{query}' — found results, but none confirmed "
                    f"{_price_bound_description(min_price, max_price)} (Amazon's own top results "
                    "skewed outside that range, often sponsored listings). Take a look at the "
                    "browser window yourself, or try a wider range."
                )

            suffix = f" {_price_bound_description(min_price, max_price)}" if has_price_filter else ""
            return f"Opened Amazon and found these for '{query}'{suffix}:\n{_format_results(results)}"
        except Exception as e:
            logger.exception("Amazon shopping search failed")
            return f"Something went wrong searching Amazon: {e}"

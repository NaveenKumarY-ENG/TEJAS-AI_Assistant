"""Open any website by name or URL in the shared browsing/shopping browser
window. tools/shopping_tool.py's ShopAmazonTool only ever navigates
amazon.in — this is the generic version, and the direct fix for the
reported bug: nothing else in the codebase ever calls .goto() on anything
but an amazon.in URL (confirmed by grepping every .goto( call in the
repo), so "open flipkart.com" (or any other site) had no tool that could
act on it at all.
"""
import difflib
import logging
import re
import urllib.parse

from config import config
from integrations import browser
from tools.base import Tool

logger = logging.getLogger("assistant.browse_tool")

# Common site names -> their real domain, so "open flipkart" / "open
# youtube" works without the user having to say the full URL. Not
# exhaustive by design — anything not listed here still works via the
# generic name + ".com" fallback in resolve_url below, and a bare domain
# ("flipkart.com") or full URL always works regardless of this list.
_SITE_ALIASES: dict[str, str] = {
    "flipkart": "www.flipkart.com",
    "myntra": "www.myntra.com",
    "amazon": "www.amazon.in",
    "youtube": "www.youtube.com",
    "google": "www.google.com",
    "gmail": "mail.google.com",
    "maps": "maps.google.com",
    "google maps": "maps.google.com",
    "wikipedia": "www.wikipedia.org",
    "github": "www.github.com",
    "reddit": "www.reddit.com",
    "linkedin": "www.linkedin.com",
    "twitter": "www.x.com",
    "x": "www.x.com",
    "instagram": "www.instagram.com",
    "facebook": "www.facebook.com",
    "netflix": "www.netflix.com",
    "spotify": "open.spotify.com",
    "whatsapp": "web.whatsapp.com",
}

_ALLOWED_SCHEMES = {"http", "https"}


class InvalidWebsite(Exception):
    pass


def resolve_url(site: str) -> str:
    """Turns a spoken/typed site name or URL into a real https:// URL to
    navigate to. Raises InvalidWebsite for empty input or anything that
    isn't a real web page (a disallowed scheme like javascript:/file:/
    data:, or a string with nothing usable in it) — this only ever opens a
    real web page, never a local/script URL."""
    site = (site or "").strip()
    if not site:
        raise InvalidWebsite("No website given.")

    # Checked via urlparse's own scheme detection, NOT just "does '://'
    # appear in the string" — javascript:/data: URIs deliberately don't
    # use "//" at all (e.g. "javascript:alert(1)"), so a substring check
    # alone would miss them entirely and let resolve_url fall through to
    # the bare-word ".com" fallback below, which is exactly wrong for a
    # script URI. Any recognized scheme (allowed or not) is decided here;
    # a plain name/domain with no scheme falls through untouched.
    parsed = urllib.parse.urlparse(site)
    if parsed.scheme:
        if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
            raise InvalidWebsite(f"'{site}' isn't a website I can open.")
        return site

    key = site.lower().strip()
    if key in _SITE_ALIASES:
        return f"https://{_SITE_ALIASES[key]}"

    # Already looks like a bare domain (has a dot, e.g. "flipkart.com" or
    # "example.co.in", and no spaces) -- use it directly rather than
    # guessing at a TLD.
    if "." in key and " " not in key:
        return f"https://{key}"

    # A likely typo of a known site name ("flipcart" for "flipkart",
    # confirmed live as a real gap) -- without this, an unrecognized bare
    # word fell straight through to the generic ".com" guess below
    # (https://flipcart.com, a real but wrong domain that just fails to
    # load) instead of the site the user almost certainly meant. A
    # conservative cutoff only catches genuinely close misspellings, not
    # just plausible-looking ones -- confirmed it does NOT misfire on an
    # unrelated bare word like "cnn".
    close_match = difflib.get_close_matches(key, _SITE_ALIASES.keys(), n=1, cutoff=0.8)
    if close_match:
        return f"https://{_SITE_ALIASES[close_match[0]]}"

    # A bare word with no known alias (or close typo of one) and no dot --
    # best-effort a ".com", same instinct a person types into an address bar.
    slug = re.sub(r"\s+", "", key)
    if not slug:
        raise InvalidWebsite(f"'{site}' isn't a website I can open.")
    return f"https://{slug}.com"


class OpenWebsiteTool(Tool):
    name = "open_website"
    description = (
        "Open any website in a real, visible browser window by name or URL (e.g. 'flipkart', "
        "'youtube', 'example.com', 'https://...'). For just opening/browsing a site with no "
        "particular search in mind. For searching or shopping on Amazon use shop_amazon, and on "
        "Flipkart use shop_flipkart instead — they run a real search rather than just opening the "
        "homepage."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "site": {
                "type": "string",
                "description": "The website to open — a name ('flipkart', 'youtube'), a bare domain ('example.com'), or a full URL.",
            },
        },
        "required": ["site"],
    }

    def run(self, site: str = "") -> str:
        if not config.live_ai_enabled:
            return "Live.AI (real-time web browsing) is disabled on this server."
        if not browser.available():
            return (
                "Browser automation isn't set up on this server — install Playwright's browser "
                "(see README.md's Setup section) to enable this."
            )
        try:
            url = resolve_url(site)
        except InvalidWebsite as e:
            return str(e)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.bring_to_front()
            return f"Opened {url} for you in a browser window."
        except Exception as e:
            logger.exception("open_website navigation failed")
            return f"Something went wrong opening {url}: {e}"

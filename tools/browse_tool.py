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
    # Confirmed live as real, reachable domains — added after a real
    # report that both silently resolved wrong: "diesel" alone was fine,
    # but "g-shock" isn't a real domain at all (the hyphen makes it look
    # like one, but the actual site is the unhyphenated "gshock.com").
    # The bare (no "www.") form specifically — confirmed live that
    # "www.gshock.com" throws a real browser cert mismatch
    # (net::ERR_CERT_COMMON_NAME_INVALID) even though it responds fine to
    # a plain curl request; the bare domain doesn't have that problem.
    "diesel": "www.diesel.com",
    "g-shock": "gshock.com",
    "gshock": "gshock.com",
    "casio": "www.casio.com",
}

# Generic descriptor words that often ride along with the actual site/brand
# name in a natural phrase ("diesel watches official website", "g-shock
# watch site") but aren't part of any real domain. Confirmed live as a real
# bug: without stripping these, the bare-word fallback below concatenated
# the WHOLE phrase into a single nonsense guess (e.g.
# "https://deiselwatchsite.com") instead of resolving just the brand name
# inside it. "watchs" (not a real word) is included because that's the
# literal phrasing confirmed live, likely a mishearing/typo of "watches."
_GENERIC_DESCRIPTOR_WORDS = frozenset(
    "website site official homepage home page store shop app online "
    "watch watches watchs the a an of for".split()
)


def _strip_generic_descriptors(key: str) -> str:
    words = [w for w in key.split() if w not in _GENERIC_DESCRIPTOR_WORDS]
    return " ".join(words) if words else key


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

    # Strip generic descriptor words ("...official website", "...watch
    # site") and re-check the alias table / a close-typo match against
    # what's left — "diesel watchs website" -> "diesel" (a real, listed
    # alias) instead of falling straight to the raw-phrase slug guess
    # below. Only takes effect if stripping actually removed something;
    # re-running the exact same checks on an unchanged key would be a
    # wasted no-op.
    cleaned_key = _strip_generic_descriptors(key)
    if cleaned_key != key and cleaned_key:
        if cleaned_key in _SITE_ALIASES:
            return f"https://{_SITE_ALIASES[cleaned_key]}"
        close_match = difflib.get_close_matches(cleaned_key, _SITE_ALIASES.keys(), n=1, cutoff=0.8)
        if close_match:
            return f"https://{_SITE_ALIASES[close_match[0]]}"
        key = cleaned_key

    # A bare word/phrase with no known alias (or close typo of one) and no
    # dot -- best-effort a ".com", same instinct a person types into an
    # address bar. Strips more than just whitespace: a hyphen in a spoken/
    # typed brand name ("g-shock") doesn't necessarily mean the real domain
    # has one too — confirmed live that "g-shock.com" isn't real but
    # "gshock.com" is — so anything that isn't a letter or digit is
    # dropped, not just collapsed.
    slug = re.sub(r"[^a-z0-9]", "", key)
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
        "homepage. If a prior web_search (or earlier tool result) already returned the real URL for "
        "what the user's asking about, pass that exact URL here — do not re-describe the site as a "
        "phrase ('diesel watches official website'); a short name or the real URL resolves far more "
        "reliably than a guessed-at description."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "site": {
                "type": "string",
                "description": (
                    "The website to open — a short name ('flipkart', 'diesel'), a bare domain "
                    "('example.com'), or a full URL. Not a descriptive phrase — if you already know "
                    "the real URL (e.g. from a prior web_search result), use that exact URL here."
                ),
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

        def _do_open():
            page = browser.new_page()
            response = page.goto(url, wait_until="domcontentloaded")
            page.bring_to_front()
            # Confirmed live as a real, separate gap: a local model can
            # pass a URL that LOOKS real (a real domain, a plausible-
            # sounding path it invented, e.g.
            # "https://www.example.com/live-cricket-score-afghan-vs-india")
            # but doesn't actually exist — page.goto() doesn't raise for
            # that, it navigates fine and lands on a 404/error page, so
            # without this check the tool reported a plain "Opened ... for
            # you" even though nothing useful actually loaded. response is
            # None only for a same-document navigation, which never
            # applies to a fresh page's first goto — guarded anyway since
            # Playwright's own docs allow it in principle.
            if response is not None and response.status >= 400:
                return (
                    f"Opened {url}, but it returned an error (HTTP {response.status}) — "
                    "that page doesn't seem to actually exist or work."
                )
            return f"Opened {url} for you in a browser window."

        try:
            # The whole unit of browser work — obtaining a page AND
            # navigating it — runs together on the single dedicated
            # browser thread (see integrations/browser.py's module
            # docstring for why calling page.goto() out here, after only
            # new_page() was dispatched, was confirmed live to still
            # break with "Cannot switch to a different thread").
            return browser.run_in_browser(_do_open)
        except Exception as e:
            logger.exception("open_website navigation failed")
            return f"Something went wrong opening {url}: {e}"

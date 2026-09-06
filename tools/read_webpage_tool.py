"""Fetch a URL and return its readable text — Live.AI's "read this page"
capability, the step research needs after web_search/shop_flipkart return a
URL worth actually reading. Reuses integrations/html_extract.py's
extraction logic (the same helper memory/knowledge.py's ingest_url already
uses for Knowledge Base URL ingestion) rather than duplicating it.
"""
import ipaddress
import logging
import socket
import urllib.parse

import requests

from config import config
from integrations.html_extract import extract_readable_text
from tools.base import Tool

logger = logging.getLogger("assistant.read_webpage_tool")

# Same cap memory/knowledge.py's ingest_url uses for the raw HTML it will
# parse — bounds parsing cost against an accidentally (or deliberately)
# huge page regardless of what the server's Content-Length header claims.
_MAX_HTML_BYTES = 2_000_000
# A tool result this large would blow out a big chunk of the model's
# context in one call — long enough to actually answer questions about the
# page, short enough not to crowd out everything else in the conversation.
_MAX_OUTPUT_CHARS = 6000
_TIMEOUT_SECONDS = 10


class UnsafeUrl(Exception):
    pass


def assert_public_host(hostname: str) -> None:
    """Basic SSRF guard. Unlike open_website (which just points a REAL,
    VISIBLE browser window the user can already see at any URL — not a
    server-side request), this tool makes the server itself issue an
    outbound requests.get() on the model's behalf, so a URL aimed at
    localhost or an internal/private address has to be rejected before
    fetching, not just trusted the way a normal web link is."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise UnsafeUrl(f"Couldn't resolve '{hostname}': {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise UnsafeUrl(f"'{hostname}' resolves to a private/internal address — refusing to fetch it.")


class ReadWebpageTool(Tool):
    name = "read_webpage"
    description = (
        "Fetch a real web page by URL and return its readable text content, for research, "
        "summarizing, or answering questions about a specific page — e.g. after web_search or "
        "shop_flipkart returns a URL that's worth actually reading. Not for Amazon/Flipkart search "
        "results pages themselves — use shop_amazon/shop_flipkart for those, they already return "
        "structured results."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full URL to fetch, e.g. 'https://example.com/article'.",
            },
        },
        "required": ["url"],
    }

    def run(self, url: str = "") -> str:
        if not config.live_ai_enabled:
            return "Live.AI (real-time web reading) is disabled on this server."
        url = (url or "").strip()
        if not url:
            return "No URL given."
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return f"'{url}' isn't a valid web page URL."
        try:
            assert_public_host(parsed.hostname)
        except UnsafeUrl as e:
            return str(e)
        try:
            response = requests.get(url, timeout=_TIMEOUT_SECONDS, headers={"User-Agent": "TEJAS-Assistant/1.0"})
            response.raise_for_status()
        except requests.RequestException as e:
            return f"Couldn't fetch {url}: {e}"
        text = extract_readable_text(response.text[:_MAX_HTML_BYTES]).strip()
        if not text:
            return f"Fetched {url}, but couldn't find any readable text on the page."
        if len(text) > _MAX_OUTPUT_CHARS:
            text = text[:_MAX_OUTPUT_CHARS] + "\n... (truncated)"
        return f"Content from {url} (just retrieved):\n\n{text}"

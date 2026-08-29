"""Real browser automation for tools/shopping_tool.py, via Playwright.

Launches a VISIBLE (headless=False) Chromium window under a dedicated,
persistent profile (data/browser_profile/) — separate from the user's
everyday Chrome, since Chrome only exposes a remote-debugging port when
launched with that flag from the start (an already-running everyday window
can't be retrofitted), and reusing the user's daily-driver window with its
unrelated tabs would be confusing anyway. A dedicated profile also means
Amazon login state (not needed for search, but relevant to any future
cart/checkout work) persists across runs once the user ever logs in.

Same lazy-singleton shape as agent/tts.py's pipeline/memory/ocr.py's reader,
with one deliberate difference: there's no warm_up() called from server.py's
startup hooks. TTS/OCR warm-up just loads a model into memory, invisible to
the user; actually launching this would pop a visible browser window on
every server start whether or not the user ever asks to shop that session —
so the one-time launch cost is paid lazily, on the first real tool call,
instead.
"""
import logging
import threading
from pathlib import Path

from config import DATA_DIR

logger = logging.getLogger("assistant.browser")

_playwright = None
_context = None
_context_lock = threading.Lock()

_availability_cache: bool | None = None
_availability_lock = threading.Lock()


def get_context():
    """Returns the shared persistent browser context, launching it (once)
    on first call. Reused for every subsequent tool call in this process —
    a new shopping query opens a new tab in the SAME window, not a second
    window."""
    global _playwright, _context
    if _context is None:
        with _context_lock:
            if _context is None:
                from playwright.sync_api import sync_playwright

                profile_dir = DATA_DIR / "browser_profile"
                profile_dir.mkdir(exist_ok=True, parents=True)
                _playwright = sync_playwright().start()
                _context = _playwright.chromium.launch_persistent_context(
                    str(profile_dir),
                    headless=False,
                    # Confirmed live: Playwright's default Chromium build sets
                    # navigator.webdriver=true and a generic UA string, and
                    # Amazon's bot-detection outright blocks that combination —
                    # a plain, unmodified launch got a "Sorry, something went
                    # wrong on our end" page every time instead of real search
                    # results. A realistic desktop UA plus masking
                    # navigator.webdriver (applied context-wide, so every new
                    # page/tab gets it automatically) was the actual fix,
                    # reproduced directly: 0 results before this, 22 after.
                    # Re-confirmed live on amazon.in (this app's actual
                    # default storefront — see shopping_tool.py/order_tool.py)
                    # after switching from amazon.com: same bypass, real
                    # results, no CAPTCHA.
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1366, "height": 900},
                )
                _context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return _context


def _compute_availability() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        # Starts and immediately stops the lightweight driver process only —
        # no browser window opens here. `pip install playwright` alone does
        # NOT download Chromium (a separate `playwright install chromium`
        # step); checking the resolved executable actually exists on disk is
        # what tells the two apart, rather than just "is the package importable."
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        logger.exception("Browser automation unavailable (Playwright not installed / chromium not downloaded)")
        return False


def available() -> bool:
    """Whether a real browser can be launched right now. Cached after the
    first call within this process."""
    global _availability_cache
    if _availability_cache is None:
        with _availability_lock:
            if _availability_cache is None:
                _availability_cache = _compute_availability()
    return _availability_cache

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

THREAD AFFINITY — read this before adding a new browser-driven tool.
Playwright's Sync API binds its driver connection to the exact OS thread
that first calls sync_playwright().start() (via an internal greenlet
switch), and EVERY later call that talks to the browser — not just the
first one — must come from that same thread, or it raises "Cannot switch
to a different thread." Every browser-driven tool reaches this module
through server.py's WebSocket handler, which runs each turn via
asyncio.to_thread(...) — that dispatches onto Python's default
ThreadPoolExecutor, whose worker-thread identity is NOT guaranteed stable
across separate calls (it reuses idle threads opportunistically, not a
single pinned one).

Confirmed live as a real, severe, 100%-reproducible bug — and confirmed
TWICE, the second time revealing the first fix was incomplete: wrapping
only new_page() (obtaining a page) in a dispatch to one dedicated thread
was NOT enough, because every tool then called page.goto()/
query_selector_all()/evaluate()/etc. directly on its OWN thread anyway —
Playwright's thread-binding applies to every single call that touches the
browser process, not just the first. The real fix: run_in_browser(fn)
below is the ONE way any tool may touch a page — a tool's entire unit of
browser work (obtain a page, navigate, extract) must happen inside one
function passed to run_in_browser, so it all runs together on the single
dedicated thread as one op. Calling browser.new_page() or a page's own
methods directly from a tool's run() — outside run_in_browser — will
intermittently (or, as confirmed live, reliably) break.
"""
import concurrent.futures
import logging
import threading
import time
from pathlib import Path

from config import DATA_DIR

logger = logging.getLogger("assistant.browser")

_playwright = None
_context = None
_context_lock = threading.Lock()

_availability_cache: bool | None = None
_availability_lock = threading.Lock()

# This ThreadPoolExecutor only ever creates and reuses ONE thread, for the
# life of the process — the stable thread identity Playwright's Sync API
# actually requires (see this module's docstring). Every Playwright touch
# in this codebase, in every browser-driven tool, funnels through
# run_in_browser() below, which submits to this executor.
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="tejas-browser")


def run_in_browser(fn, *args, **kwargs):
    """Runs fn(*args, **kwargs) — and every Playwright call fn makes,
    transitively, however many there are (navigate, query, extract,
    click...) — on the single dedicated browser thread, and returns its
    result (or re-raises its exception) to the calling thread. This is
    the one and only way tool code in tools/ should touch the browser:
    wrap an entire unit of work (everything from get_context()/new_page()
    through the last page.method() call needed) in one function and pass
    it here, rather than calling new_page() or a page's methods directly.
    See this module's docstring for why a narrower fix (dispatching only
    the "get a page" step) was confirmed live to still break."""
    return _executor.submit(fn, *args, **kwargs).result()


# How many times new_page() will relaunch-and-retry after the browser looks
# closed, and how long it waits between attempts. Not just one immediate
# retry: confirmed live (a real lockfile found sitting in
# data/browser_profile/, timestamped alongside the profile's other
# most-recently-touched files) that Chromium's own profile-singleton lock
# can still be held for a moment after the user closes the visible window —
# a multi-process browser tears its subprocesses down in sequence, not
# instantly, and on Windows a file handle can outlive the window closing by
# a beat. A relaunch attempted with zero delay can race that teardown and
# fail even though the old browser really is on its way out; a short pause
# between attempts gives it time to actually finish.
_RELAUNCH_ATTEMPTS = 3
_RELAUNCH_DELAY_SECONDS = 1.5


def get_context():
    """Returns the shared persistent browser context, launching it (once)
    on first call. Reused for every subsequent tool call in this process —
    a new shopping query opens a new tab in the SAME window, not a second
    window.

    Only ever safe to call from inside a run_in_browser-dispatched
    function (directly, or via new_page() below) — see this module's
    docstring."""
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


def new_page():
    """Returns a new tab in the shared persistent browser context —
    relaunching the browser automatically if it was closed since the last
    call, instead of every tool call failing forever until the whole
    server is restarted.

    Only ever safe to call from inside a run_in_browser-dispatched
    function — this does NOT dispatch itself (doing so would let a tool
    call it directly from the wrong thread and then keep working with the
    returned page on that same wrong thread anyway, which is the exact
    bug this whole module's docstring describes). Every tool's run()
    should look like:
        def _do_work(...):
            page = browser.new_page()
            page.goto(...)
            ...
            return result
        return browser.run_in_browser(_do_work, ...)

    Confirmed live as a real, recurring failure: launch_persistent_context
    above opens a real, visible window with no "keep running after the
    last window closes" flag, so Chromium's own default behavior is to
    exit the whole browser process once the user closes that window
    manually — but the cached _context singleton above has no way to know
    that happened on its own, so every subsequent get_context().new_page()
    call raised (Playwright's own "has been closed" error) with no
    recovery, only masked as a generic tool failure by whichever tool
    happened to call it.

    Relaunching reuses the same on-disk profile_dir get_context() already
    points at, so this does NOT lose Amazon login state — it's persisted
    to disk, not held only in the dead process's memory. Retries up to
    _RELAUNCH_ATTEMPTS times with a short pause between (see that
    constant's own comment for why a single zero-delay retry wasn't
    enough) — a failure on every attempt propagates normally to the
    caller's own except clause, same "don't silently swallow a real
    failure" contract as everywhere else in this codebase.

    get_context() is called INSIDE the try, not before it — confirmed
    live as a real, separate bug: get_context() itself can raise (a
    corrupted leftover Playwright connection failing to relaunch, for
    instance), and when that line sat above the try, such a failure
    skipped the reset-and-retry logic entirely, leaving _playwright/
    _context stuck in a broken state no later call could ever recover
    from on its own."""
    global _playwright, _context
    last_error: Exception | None = None
    for attempt in range(_RELAUNCH_ATTEMPTS):
        try:
            context = get_context()
            return context.new_page()
        except Exception as e:
            last_error = e
            logger.warning(
                "Browser context looks closed (attempt %d/%d) — relaunching: %s",
                attempt + 1,
                _RELAUNCH_ATTEMPTS,
                e,
            )
            with _context_lock:
                _context = None
                if _playwright is not None:
                    try:
                        _playwright.stop()
                    except Exception:
                        pass
                    _playwright = None
            if attempt < _RELAUNCH_ATTEMPTS - 1:
                time.sleep(_RELAUNCH_DELAY_SECONDS)
    assert last_error is not None  # the loop only exits early via `return`
    raise last_error


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
    first call within this process. Dispatched via run_in_browser just
    like any other Playwright touch — this one's self-contained (a single
    short-lived sync_playwright() block with no further calls needed), but
    routing it through the same dedicated thread as everything else
    removes any doubt about Playwright's "inside an asyncio loop" check."""
    global _availability_cache
    if _availability_cache is None:
        with _availability_lock:
            if _availability_cache is None:
                _availability_cache = run_in_browser(_compute_availability)
    return _availability_cache

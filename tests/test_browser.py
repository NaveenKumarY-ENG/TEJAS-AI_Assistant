"""
Tests for integrations/browser.py's new_page() — the auto-heal-on-a-closed-
browser retry-with-backoff logic. No real Playwright involved; fakes stand
in for the sync_playwright()/BrowserContext objects, and time.sleep is
patched out so the suite doesn't actually pause between retries.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from integrations import browser


@pytest.fixture(autouse=True)
def _reset_singleton():
    """_context/_playwright are module-level singleton state — isolate
    each test from whatever an earlier one left behind."""
    browser._context = None
    browser._playwright = None
    yield
    browser._context = None
    browser._playwright = None


@pytest.fixture(autouse=True)
def mock_sleep():
    """new_page() pauses _RELAUNCH_DELAY_SECONDS between retry attempts —
    real in production, pure waste in a test suite. Patched out globally
    here rather than per-test; tests that care how many times it was
    called take this fixture as a parameter."""
    with patch.object(browser.time, "sleep") as m:
        yield m


class FakePlaywrightHandle:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeContext:
    def __init__(self, page_or_error):
        # A callable/exception-raising new_page, or a plain page to return.
        self._page_or_error = page_or_error
        self.new_page_calls = 0

    def new_page(self):
        self.new_page_calls += 1
        if isinstance(self._page_or_error, Exception):
            raise self._page_or_error
        return self._page_or_error


def test_new_page_returns_a_page_from_a_healthy_context():
    fake_page = object()
    healthy_context = FakeContext(fake_page)
    with patch.object(browser, "get_context", return_value=healthy_context) as mock_get_context:
        result = browser.new_page()
    assert result is fake_page
    assert healthy_context.new_page_calls == 1
    mock_get_context.assert_called_once()


def test_run_in_browser_always_runs_on_the_same_dedicated_thread():
    """The actual regression test for the real, live-reported bug —
    confirmed live TWICE, the second time revealing an earlier version of
    this fix (dispatching only new_page() itself) was incomplete: a tool
    that obtained a page via a dispatched new_page() and then called
    page.goto()/etc. directly, undispatched, still broke with "Cannot
    switch to a different thread" — Playwright's thread-binding applies to
    every call that touches the browser, not just the first. Every tool
    now wraps its ENTIRE unit of browser work (new_page() through the last
    page.method() call) in one function passed to run_in_browser — this
    test simulates exactly that shape (a function that calls new_page()
    AND then calls a method on the page it got back) from several
    different calling threads, and confirms it all lands on browser.py's
    one dedicated thread every time."""
    import threading

    seen_thread_ids: list[int] = []

    class FakePage:
        def goto(self, url):
            # The regression case: a second Playwright call, on the page
            # new_page() already returned — must land on the SAME thread
            # new_page() itself ran on, not the caller's thread.
            seen_thread_ids.append(threading.get_ident())

    class FakeContext:
        def new_page(self):
            seen_thread_ids.append(threading.get_ident())
            return FakePage()

    def do_browser_work():
        page = browser.new_page()
        page.goto("https://example.com")
        return "ok"

    with patch.object(browser, "get_context", return_value=FakeContext()):
        results = []

        def call_from_a_fresh_thread():
            results.append(browser.run_in_browser(do_browser_work))

        # Deliberately call run_in_browser() from several DIFFERENT calling
        # threads (simulating asyncio.to_thread's pool handing out
        # different worker threads across turns) — every one of them, and
        # every Playwright call made inside do_browser_work, must still
        # land on browser.py's own single dedicated thread.
        for _ in range(5):
            t = threading.Thread(target=call_from_a_fresh_thread)
            t.start()
            t.join()

    assert results == ["ok"] * 5
    # 2 recorded thread-ids per call (new_page() + goto()) x 5 calls.
    assert len(seen_thread_ids) == 10
    assert len(set(seen_thread_ids)) == 1, "browser work executed on more than one thread across calls"
    # And that one shared thread must not be any of the (all different)
    # calling threads above — confirming real dispatch happened, not a
    # same-thread pass-through that would coincidentally look stable too.
    assert threading.get_ident() not in seen_thread_ids


def test_new_page_relaunches_when_the_context_was_closed():
    """The core regression this exists for: a real browser window closed
    manually (or crashed) leaves _context pointing at a dead context —
    new_page() must detect the failure, reset the singleton, and get a
    fresh working context rather than failing forever."""
    dead_context = FakeContext(RuntimeError("Target page, context or browser has been closed"))
    healthy_context = FakeContext(object())
    fake_playwright_handle = FakePlaywrightHandle()
    browser._context = dead_context
    browser._playwright = fake_playwright_handle

    call_count = {"n": 0}

    def fake_get_context():
        call_count["n"] += 1
        # First call (inside new_page()'s initial `context = get_context()`)
        # returns the already-dead context set up above; the relaunch call
        # after the reset returns a fresh healthy one.
        return dead_context if call_count["n"] == 1 else healthy_context

    with patch.object(browser, "get_context", side_effect=fake_get_context):
        result = browser.new_page()

    assert result is healthy_context._page_or_error
    assert dead_context.new_page_calls == 1
    assert healthy_context.new_page_calls == 1
    assert fake_playwright_handle.stopped is True


def test_new_page_retries_more_than_once_before_giving_up(mock_sleep):
    """Regression test for the actual gap in the previous (single-retry)
    version: a still-tearing-down old browser process can plausibly still
    be holding its profile lock on the very next attempt too — new_page()
    must survive that by trying more than twice, with a pause between."""
    dying_context = FakeContext(RuntimeError("profile lock still held"))
    with patch.object(browser, "get_context", return_value=dying_context):
        with pytest.raises(RuntimeError, match="profile lock still held"):
            browser.new_page()

    assert dying_context.new_page_calls == browser._RELAUNCH_ATTEMPTS
    # A pause between every attempt except the last one.
    assert mock_sleep.call_count == browser._RELAUNCH_ATTEMPTS - 1


def test_new_page_succeeds_on_the_final_attempt():
    """Fails twice, then a relaunch on the third attempt finally works —
    confirms the retry loop doesn't give up early."""
    attempts = [
        FakeContext(RuntimeError("still locked")),
        FakeContext(RuntimeError("still locked")),
        FakeContext(object()),
    ]
    assert browser._RELAUNCH_ATTEMPTS == len(attempts)
    call_count = {"n": 0}

    def fake_get_context():
        result = attempts[call_count["n"]]
        call_count["n"] += 1
        return result

    with patch.object(browser, "get_context", side_effect=fake_get_context):
        result = browser.new_page()

    assert result is attempts[-1]._page_or_error
    assert all(c.new_page_calls == 1 for c in attempts)


def test_new_page_propagates_the_last_error_when_every_attempt_fails():
    """No infinite retry, no silent swallow — a persistent real failure
    (not just a transiently stale singleton) must still surface to the
    caller after exhausting all attempts."""
    dead_context = FakeContext(RuntimeError("still dead"))
    with patch.object(browser, "get_context", return_value=dead_context):
        with pytest.raises(RuntimeError, match="still dead"):
            browser.new_page()
    assert dead_context.new_page_calls == browser._RELAUNCH_ATTEMPTS


def test_new_page_tolerates_playwright_stop_itself_raising():
    """The old playwright driver handle's own .stop() can fail too (the
    process it was talking to may already be gone) — that must not stop
    the relaunch from being attempted."""

    class BrokenStopHandle:
        def stop(self):
            raise RuntimeError("already gone")

    dead_context = FakeContext(RuntimeError("closed"))
    healthy_context = FakeContext(object())
    browser._context = dead_context
    browser._playwright = BrokenStopHandle()

    call_count = {"n": 0}

    def fake_get_context():
        call_count["n"] += 1
        return dead_context if call_count["n"] == 1 else healthy_context

    with patch.object(browser, "get_context", side_effect=fake_get_context):
        result = browser.new_page()

    assert result is healthy_context._page_or_error

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

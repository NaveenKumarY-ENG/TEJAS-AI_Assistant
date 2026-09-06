"""
Tests for tools/read_webpage_tool.py (read_webpage). requests.get is mocked
throughout — no real network calls, same mocking style as
tests/test_web_search.py. socket.getaddrinfo is mocked for the SSRF-guard
tests so they don't depend on real DNS resolution either.
"""
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import read_webpage_tool
from tools.read_webpage_tool import ReadWebpageTool, UnsafeUrl, assert_public_host

tool = ReadWebpageTool()


def _addrinfo(ip: str):
    # Shape of a single socket.getaddrinfo() result tuple: (family, type,
    # proto, canonname, sockaddr) — only sockaddr[0] (the IP) is read.
    return [(2, 1, 6, "", (ip, 443))]


# --- assert_public_host (SSRF guard) ---


def test_assert_public_host_rejects_loopback():
    with patch.object(read_webpage_tool.socket, "getaddrinfo", return_value=_addrinfo("127.0.0.1")):
        with pytest.raises(UnsafeUrl):
            assert_public_host("localhost")


def test_assert_public_host_rejects_private_range():
    with patch.object(read_webpage_tool.socket, "getaddrinfo", return_value=_addrinfo("192.168.1.5")):
        with pytest.raises(UnsafeUrl):
            assert_public_host("internal.example")


def test_assert_public_host_rejects_link_local():
    with patch.object(read_webpage_tool.socket, "getaddrinfo", return_value=_addrinfo("169.254.169.254")):
        with pytest.raises(UnsafeUrl):
            assert_public_host("metadata.internal")


def test_assert_public_host_accepts_a_real_public_ip():
    with patch.object(read_webpage_tool.socket, "getaddrinfo", return_value=_addrinfo("93.184.216.34")):
        assert_public_host("example.com")  # must not raise


def test_assert_public_host_surfaces_dns_failure():
    import socket as socket_module

    with patch.object(
        read_webpage_tool.socket, "getaddrinfo", side_effect=socket_module.gaierror("no such host")
    ):
        with pytest.raises(UnsafeUrl):
            assert_public_host("nonexistent.invalid")


# --- ReadWebpageTool.run ---


def test_run_returns_disabled_message_when_live_ai_disabled():
    with patch.object(read_webpage_tool.config, "live_ai_enabled", False):
        result = tool.run(url="https://example.com")
    assert "disabled" in result.lower()


def test_run_rejects_empty_url():
    with patch.object(read_webpage_tool.config, "live_ai_enabled", True):
        result = tool.run(url="")
    assert "No URL given" in result


def test_run_rejects_disallowed_scheme():
    with patch.object(read_webpage_tool.config, "live_ai_enabled", True):
        result = tool.run(url="ftp://example.com/file")
    assert "isn't a valid web page URL" in result


def test_run_refuses_ssrf_target_without_fetching():
    with patch.object(read_webpage_tool.config, "live_ai_enabled", True), patch.object(
        read_webpage_tool, "assert_public_host", side_effect=UnsafeUrl("nope")
    ), patch.object(read_webpage_tool.requests, "get") as mock_get:
        result = tool.run(url="http://169.254.169.254/latest/meta-data")
    assert "nope" in result
    mock_get.assert_not_called()


def test_run_returns_extracted_text_on_success():
    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    fake_response.text = "<html><body><main><p>Comet Halley returns in 2061.</p></main></body></html>"
    with patch.object(read_webpage_tool.config, "live_ai_enabled", True), patch.object(
        read_webpage_tool, "assert_public_host"
    ), patch.object(read_webpage_tool.requests, "get", return_value=fake_response):
        result = tool.run(url="https://example.com/article")
    assert "Comet Halley returns in 2061." in result
    assert "just retrieved" in result


def test_run_handles_page_with_no_readable_text():
    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    fake_response.text = "<html><body><script>1</script></body></html>"
    with patch.object(read_webpage_tool.config, "live_ai_enabled", True), patch.object(
        read_webpage_tool, "assert_public_host"
    ), patch.object(read_webpage_tool.requests, "get", return_value=fake_response):
        result = tool.run(url="https://example.com/blank")
    assert "couldn't find any readable text" in result


def test_run_surfaces_request_errors_instead_of_raising():
    with patch.object(read_webpage_tool.config, "live_ai_enabled", True), patch.object(
        read_webpage_tool, "assert_public_host"
    ), patch.object(
        read_webpage_tool.requests, "get", side_effect=requests.ConnectionError("network unreachable")
    ):
        result = tool.run(url="https://example.com")
    assert "Couldn't fetch" in result


def test_run_truncates_very_long_pages():
    fake_response = Mock()
    fake_response.raise_for_status = Mock()
    long_paragraph = "word " * 3000
    fake_response.text = f"<html><body><p>{long_paragraph}</p></body></html>"
    with patch.object(read_webpage_tool.config, "live_ai_enabled", True), patch.object(
        read_webpage_tool, "assert_public_host"
    ), patch.object(read_webpage_tool.requests, "get", return_value=fake_response):
        result = tool.run(url="https://example.com/long")
    assert result.endswith("... (truncated)")
    assert len(result) < len(long_paragraph) + 200

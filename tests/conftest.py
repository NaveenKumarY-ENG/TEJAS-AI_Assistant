"""
Points the whole test session at an isolated, throwaway data directory
instead of the real database — must run before ANY test module (or
anything they import) touches config.py, since memory/vector.py's
ChromaDB client and memory/structured.py's SQLite connection both resolve
their paths from config at import time. pytest always loads a directory's
conftest.py before collecting its test files, so setting the env var here,
at module scope, is early enough.

Without this, a test like "a nonsense query returns no results" is only as
reliable as whatever real data happens to be in the live knowledge base at
the moment the suite runs — confirmed flaky twice already (once against a
garbled OCR chunk, again against a real uploaded PDF).
"""
import os
import shutil
import tempfile
from unittest.mock import patch

import pytest

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="tejas_test_data_")
os.environ["TEJAS_DATA_DIR"] = _TEST_DATA_DIR


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_real_structured_extraction(request):
    """Structured extraction (memory/extraction.py) now runs on every
    ingest_document/ingest_url/ingest_note call. Without this, every
    existing knowledge-base test would either depend on a real local Ollama
    instance being up (slow, flaky) or silently get no structured data if
    it isn't. Mocked to a clean no-op by default.

    Skipped for test_extraction.py itself — those tests exercise
    extract_structured_fields() directly (mocking ollama.chat underneath
    it), so replacing the function itself here would make their own mocking
    pointless. The structured-specific tests in test_knowledge.py instead
    override this fixture's patch locally with their own `with patch(...)`
    block, which takes precedence for their scope — that still works fine
    since they go through memory.knowledge, not memory.extraction directly."""
    if request.module.__name__ == "test_extraction":
        yield
        return
    with patch("memory.knowledge.extraction.extract_structured_fields", return_value={}):
        yield

"""
Tests for structured field extraction (memory/extraction.py) — mocks
ollama.chat at the boundary, same pattern as other tests in this suite mock
external calls (requests.get for URL ingestion, ocr.* for image/PDF OCR).
Never hits a real Ollama server.

Run with: pytest tests/
"""
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory import extraction


def _fake_ollama_response(content: str) -> Mock:
    return {"message": {"content": content}}


def test_extract_structured_fields_valid_json():
    with patch("ollama.chat", return_value=_fake_ollama_response(
        '{"Name": "Jane Doe", "Date of Birth": "01-01-1990", "_document_type": "Aadhaar Card"}'
    )):
        result = extraction.extract_structured_fields("some document text")
    assert result == {"Name": "Jane Doe", "Date of Birth": "01-01-1990", "_document_type": "Aadhaar Card"}


def test_extract_structured_fields_drops_a_bare_year_as_a_date_of_birth():
    """Regression test for a real bug found live: a re-uploaded Aadhaar
    card extracted "Date of Birth": "1947" — a bare 4-digit year, not a
    real date — which the extraction prompt's own "omit garbled values"
    instruction was supposed to catch but didn't (the same instruction-
    following gap already seen elsewhere in this codebase). A field whose
    name is recognized (date of birth, Aadhaar number, PAN, PIN code,
    mobile) now gets a deterministic shape check instead of being trusted
    outright; other fields are untouched, still relying on the prompt."""
    with patch("ollama.chat", return_value=_fake_ollama_response(
        '{"Name": "Naveen Kumar", "Date of Birth": "1947", "_document_type": "Aadhaar Card"}'
    )):
        result = extraction.extract_structured_fields("some document text")
    assert "Date of Birth" not in result
    assert result["Name"] == "Naveen Kumar"  # unrelated, unvalidated fields pass through untouched
    assert result["_document_type"] == "Aadhaar Card"


def test_extract_structured_fields_keeps_a_properly_formatted_date_of_birth():
    with patch("ollama.chat", return_value=_fake_ollama_response(
        '{"Date of Birth": "04/08/1998", "_document_type": "Aadhaar Card"}'
    )):
        result = extraction.extract_structured_fields("some document text")
    assert result["Date of Birth"] == "04/08/1998"


def test_extract_structured_fields_validates_known_id_field_shapes():
    with patch("ollama.chat", return_value=_fake_ollama_response(
        '{"Aadhaar Number": "2501 4143 9352", "PAN Number": "ABCDE1234F", '
        '"PIN Code": "560064", "Mobile": "9480284664", "_document_type": "Aadhaar Card"}'
    )):
        result = extraction.extract_structured_fields("some document text")
    assert result["Aadhaar Number"] == "2501 4143 9352"
    assert result["PAN Number"] == "ABCDE1234F"
    assert result["PIN Code"] == "560064"
    assert result["Mobile"] == "9480284664"


def test_extract_structured_fields_drops_malformed_known_id_field_shapes():
    with patch("ollama.chat", return_value=_fake_ollama_response(
        # Aadhaar Number too short, PIN Code has letters, Mobile too short.
        '{"Aadhaar Number": "2501 4143", "PIN Code": "5600A4", "Mobile": "94802846", '
        '"_document_type": "Aadhaar Card"}'
    )):
        result = extraction.extract_structured_fields("some document text")
    assert result == {"_document_type": "Aadhaar Card"}


def test_extract_structured_fields_general_document():
    with patch("ollama.chat", return_value=_fake_ollama_response('{"_document_type": "General Document"}')):
        result = extraction.extract_structured_fields("just a prose paragraph with no fields")
    assert result == {"_document_type": "General Document"}


def test_extract_structured_fields_malformed_json_returns_empty():
    with patch("ollama.chat", return_value=_fake_ollama_response("not valid json at all")):
        result = extraction.extract_structured_fields("some text")
    assert result == {}


def test_extract_structured_fields_non_dict_json_returns_empty():
    with patch("ollama.chat", return_value=_fake_ollama_response('["not", "a", "dict"]')):
        result = extraction.extract_structured_fields("some text")
    assert result == {}


def test_extract_structured_fields_ollama_unreachable_returns_empty():
    with patch("ollama.chat", side_effect=ConnectionError("no route to host")):
        result = extraction.extract_structured_fields("some text")
    assert result == {}


def test_extract_structured_fields_truncates_long_input():
    captured = {}

    def fake_chat(**kwargs):
        captured["text_len"] = len(kwargs["messages"][0]["content"])
        return _fake_ollama_response('{"_document_type": "General Document"}')

    with patch("ollama.chat", side_effect=fake_chat):
        extraction.extract_structured_fields("x" * 50_000)
    # The prompt template adds its own fixed text around the truncated
    # document body, so just confirm the input didn't get sent in full.
    assert captured["text_len"] < 50_000

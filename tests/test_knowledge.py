"""
Tests for the knowledge base (memory/knowledge.py) and its tool wrapper.
Run with: pytest tests/
"""
import io
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory import knowledge, structured
from tools.knowledge_tool import SearchKnowledgeTool


def test_chunk_text_empty():
    assert knowledge._chunk_text("") == []
    assert knowledge._chunk_text("   ") == []


def test_chunk_text_short_stays_one_chunk():
    chunks = knowledge._chunk_text("short text", size=800, overlap=100)
    assert chunks == ["short text"]


def test_chunk_text_overlap():
    text = "a" * 1000
    chunks = knowledge._chunk_text(text, size=400, overlap=50)
    assert len(chunks) == 3
    assert len(chunks[0]) == 400
    # Consecutive chunks actually overlap by the requested amount.
    assert chunks[0][-50:] == chunks[1][:50]


def test_extract_text_unsupported_type():
    try:
        knowledge._extract_text("file.xyz", b"data")
        assert False, "expected UnsupportedFileType"
    except knowledge.UnsupportedFileType:
        pass


def test_ingest_search_delete_round_trip():
    doc = knowledge.ingest_document(
        "test_notes.txt", b"The launch code is zebra-quartz-77. Keep it secret."
    )
    try:
        assert doc["filename"] == "test_notes.txt"
        assert doc["chunk_count"] == 1

        results = knowledge.search("what is the launch code")
        assert any("zebra-quartz-77" in r["text"] for r in results)
        assert results[0]["filename"] == "test_notes.txt"
    finally:
        assert knowledge.delete_document(doc["id"]) is True

    # Deleted document's content should no longer be findable.
    assert knowledge.delete_document(doc["id"]) is False


def test_ingest_empty_document_raises():
    try:
        knowledge.ingest_document("empty.txt", b"   ")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_search_knowledge_tool_no_results():
    tool = SearchKnowledgeTool()
    result = tool.run(query="something nobody has ever uploaded xyzzy12345")
    assert "No relevant documents" in result


def test_search_knowledge_tool_finds_uploaded_content():
    doc = knowledge.ingest_document("tool_test.txt", b"Project Nightingale ships in October.")
    try:
        tool = SearchKnowledgeTool()
        result = tool.run(query="when does project nightingale ship")
        assert "October" in result
        assert "tool_test.txt" in result
    finally:
        knowledge.delete_document(doc["id"])


def test_extract_text_docx():
    from docx import Document

    doc = Document()
    doc.add_paragraph("The vault combination is 14-27-8.")
    buf = io.BytesIO()
    doc.save(buf)

    text = knowledge._extract_text("notes.docx", buf.getvalue())
    assert "14-27-8" in text


def test_extract_text_from_html_strips_boilerplate():
    html = """
    <html><head><style>body{color:red}</style></head>
    <body>
      <nav>Home | About | Contact</nav>
      <header>Site Header</header>
      <main><h1>Article</h1><p>The secret ingredient is saffron.</p></main>
      <footer>Copyright 2026</footer>
    </body></html>
    """
    text = knowledge._extract_text_from_html(html)
    assert "saffron" in text
    assert "Home" not in text
    assert "Copyright" not in text


def test_ingest_url_round_trip():
    fake_response = Mock()
    fake_response.text = "<html><body><main><p>Comet Halley returns in 2061.</p></main></body></html>"
    fake_response.raise_for_status = Mock()

    with patch("memory.knowledge.requests.get", return_value=fake_response) as mock_get:
        doc = knowledge.ingest_url("https://example.com/comet")
        mock_get.assert_called_once()

    try:
        assert doc["filename"] == "https://example.com/comet"
        results = knowledge.search("when does Halley's comet return")
        assert any("2061" in r["text"] for r in results)
    finally:
        knowledge.delete_document(doc["id"])


def test_ingest_url_fetch_failure_propagates():
    import requests

    with patch("memory.knowledge.requests.get", side_effect=requests.ConnectionError("no route")):
        try:
            knowledge.ingest_url("https://unreachable.example")
            assert False, "expected requests.ConnectionError"
        except requests.ConnectionError:
            pass


def test_tags_round_trip_through_ingest_and_list():
    doc = knowledge.ingest_document("tagged.txt", b"Some tagged content here.", tags=["work", "q3"])
    try:
        assert doc["tags"] == ["work", "q3"]
        listed = structured.list_documents()
        found = next(d for d in listed if d["id"] == doc["id"])
        assert found["tags"] == ["work", "q3"]
    finally:
        knowledge.delete_document(doc["id"])


def test_update_tags():
    doc = knowledge.ingest_document("retag_me.txt", b"Content that will be retagged.")
    try:
        assert knowledge.update_tags(doc["id"], ["renamed", "important"]) is True
        listed = structured.list_documents()
        found = next(d for d in listed if d["id"] == doc["id"])
        assert found["tags"] == ["renamed", "important"]
    finally:
        knowledge.delete_document(doc["id"])


def test_update_tags_nonexistent_document():
    assert knowledge.update_tags(999_999_999, ["x"]) is False


def test_ingest_note_round_trip():
    doc = knowledge.ingest_note("Meeting Notes", "Discuss Q3 roadmap and budget.", tags=["work"])
    try:
        assert doc["filename"] == "Meeting Notes"
        assert doc["tags"] == ["work"]
        results = knowledge.search("what should we discuss about budget")
        assert any("Q3 roadmap" in r["text"] for r in results)
    finally:
        knowledge.delete_document(doc["id"])


def test_ingest_note_empty_title_raises():
    try:
        knowledge.ingest_note("   ", "some text")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_ingest_note_empty_text_raises():
    try:
        knowledge.ingest_note("Title", "   ")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_ingest_image_raises_ocr_unavailable_when_ocr_missing():
    with patch("memory.knowledge.ocr.available", return_value=False):
        try:
            knowledge.ingest_document("photo.png", b"fake png bytes")
            assert False, "expected OCRUnavailable"
        except knowledge.OCRUnavailable:
            pass


def test_ingest_image_uses_ocr_when_available():
    with patch("memory.knowledge.ocr.available", return_value=True), patch(
        "memory.knowledge.ocr.image_to_text", return_value="The rendezvous is at midnight."
    ):
        doc = knowledge.ingest_document("photo.png", b"fake png bytes")
    try:
        assert doc["filename"] == "photo.png"
        results = knowledge.search("when is the rendezvous")
        assert any("midnight" in r["text"] for r in results)
    finally:
        knowledge.delete_document(doc["id"])


def test_pdf_falls_back_to_ocr_for_scanned_pages():
    """A page pypdf can't extract real text from (scanned/image-only) should
    get rendered and OCR'd instead of silently coming back blank."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)  # no text layer at all
    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()

    with patch("memory.knowledge.ocr.available", return_value=True), patch(
        "memory.knowledge.ocr.pdf_page_to_image_bytes", return_value=b"fake page image"
    ), patch("memory.knowledge.ocr.image_to_text", return_value="Scanned contents: safe combination 9-9-9."):
        doc = knowledge.ingest_document("scanned.pdf", pdf_bytes)
    try:
        results = knowledge.search("what is the safe combination")
        assert any("9-9-9" in r["text"] for r in results)
    finally:
        knowledge.delete_document(doc["id"])


def test_search_filters_out_irrelevant_matches():
    """search() must not just return its nearest neighbor regardless of
    relevance — a query about something the knowledge base has nothing on
    should come back empty, not with the closest-available unrelated chunk.
    Regression test for the bug where a single noisy/garbled chunk (e.g. OCR
    output) embedded to a spuriously "central" vector and surfaced as the
    top match for completely unrelated queries."""
    doc = knowledge.ingest_document(
        "earnings.txt",
        b"The quarterly earnings report shows revenue grew twelve percent, "
        b"driven mainly by strong cloud subscription renewals in the enterprise segment.",
    )
    try:
        assert knowledge.search("what is the capital of France") == []
        assert knowledge.search("recipe for chocolate cake") == []
        # A genuinely relevant, differently-worded query still finds it —
        # confirms the threshold isn't so tight it breaks real recall.
        results = knowledge.search("how did revenue perform this quarter")
        assert any("twelve percent" in r["text"] for r in results)
    finally:
        knowledge.delete_document(doc["id"])


def test_pdf_stays_blank_for_scanned_pages_when_ocr_unavailable():
    """Unchanged pre-Phase-4 behavior: no OCR available means a scanned page
    just contributes no text, not an error — a real-text PDF elsewhere in
    the same file still ingests fine."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)

    with patch("memory.knowledge.ocr.available", return_value=False):
        try:
            knowledge.ingest_document("blank_scan.pdf", buf.getvalue())
            assert False, "expected ValueError (no extractable text at all)"
        except ValueError:
            pass

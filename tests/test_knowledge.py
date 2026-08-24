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


def test_ingest_document_threads_through_structured_data():
    fake_fields = {"Name": "Jane Doe", "Aadhaar Number": "1234 5678 9012", "_document_type": "Aadhaar Card"}
    with patch("memory.knowledge.extraction.extract_structured_fields", return_value=dict(fake_fields)):
        doc = knowledge.ingest_document("id_card.txt", b"some OCR'd ID card text")
    try:
        assert doc["doc_type"] == "Aadhaar Card"
        assert doc["structured_data"] == {"Name": "Jane Doe", "Aadhaar Number": "1234 5678 9012"}
        stored = structured.get_document(doc["id"])
        assert stored["doc_type"] == "Aadhaar Card"
        assert stored["structured_data"] == {"Name": "Jane Doe", "Aadhaar Number": "1234 5678 9012"}
    finally:
        knowledge.delete_document(doc["id"])


def test_ingest_document_with_no_structured_fields_found():
    with patch("memory.knowledge.extraction.extract_structured_fields", return_value={"_document_type": "General Document"}):
        doc = knowledge.ingest_document("essay.txt", b"just a plain prose paragraph")
    try:
        assert doc["doc_type"] == "General Document"
        assert doc["structured_data"] == {}
    finally:
        knowledge.delete_document(doc["id"])


def test_format_structured_table_renders_markdown_table():
    doc = {
        "filename": "id_card.txt",
        "doc_type": "Aadhaar Card",
        "structured_data": {"Name": "Jane Doe", "Aadhaar Number": "1234 5678 9012"},
    }
    table = knowledge.format_structured_table(doc)
    assert "**id_card.txt** (Aadhaar Card)" in table
    assert "| Name | Jane Doe |" in table
    assert "| Aadhaar Number | 1234 5678 9012 |" in table


def test_format_structured_table_casts_non_string_values():
    doc = {"filename": "form.txt", "doc_type": "", "structured_data": {"Amount": 42, "Approved": True}}
    table = knowledge.format_structured_table(doc)
    assert "| Amount | 42 |" in table
    assert "| Approved | True |" in table


def test_format_structured_table_sanitizes_newlines_and_pipes():
    """Regression test: a real newline or "|" inside a cell breaks Markdown
    table syntax outright — confirmed live, the model worked around a
    multi-line address by inserting literal "<br>" tags that the chat UI
    doesn't render as HTML, just as visible junk text. Every row must stay
    on one line no matter what the extracted value looks like."""
    doc = {
        "filename": "id.jpg",
        "doc_type": "Aadhaar Card",
        "structured_data": {"Address": "SH4OHABAD ROLD\nLEE Aasm\n60410", "Note": "A | B"},
    }
    table = knowledge.format_structured_table(doc)
    assert "\n" not in table.split("| Address |")[1].split("\n")[0]
    assert "SH4OHABAD ROLD, LEE Aasm, 60410" in table
    assert "A / B" in table
    # Every non-blank line must itself be a single well-formed table row.
    for line in table.splitlines():
        assert line.count("\n") == 0


def test_document_listing_is_names_and_types_only():
    """Regression test: the user explicitly wants only filenames/types for
    "what's in my knowledge base", not full extracted details — confirm the
    listing never includes structured_data field values."""
    with patch("memory.knowledge.extraction.extract_structured_fields", return_value={"Name": "Jane Doe", "_document_type": "ID Card"}):
        doc = knowledge.ingest_document("id_card.txt", b"some content")
    try:
        listing = knowledge.document_listing()
        assert "id_card.txt (ID Card)" in listing
        assert "Jane Doe" not in listing
    finally:
        knowledge.delete_document(doc["id"])


def test_document_listing_empty_when_no_documents():
    assert knowledge.document_listing() == ""


def test_search_returns_structured_table_for_documents_with_fields():
    fake_fields = {"Name": "Jane Doe", "PAN Number": "ABCDE1234F", "_document_type": "PAN Card"}
    with patch("memory.knowledge.extraction.extract_structured_fields", return_value=dict(fake_fields)):
        doc = knowledge.ingest_document("pan_card.txt", b"some OCR'd PAN card text mentioning Jane Doe")
    try:
        results = knowledge.search("what is on the PAN card")
        assert len(results) == 1
        assert results[0]["filename"] == "pan_card.txt"
        assert "| Name | Jane Doe |" in results[0]["text"]
        assert "| PAN Number | ABCDE1234F |" in results[0]["text"]
        # The raw OCR text must NOT leak through once structured data exists.
        assert "some OCR'd PAN card text" not in results[0]["text"]
    finally:
        knowledge.delete_document(doc["id"])


def test_search_still_returns_raw_text_for_unstructured_documents():
    doc = knowledge.ingest_document("notes.txt", b"The launch code is zebra-quartz-77.")
    try:
        results = knowledge.search("what is the launch code")
        assert len(results) == 1
        assert results[0]["text"] == "The launch code is zebra-quartz-77."
    finally:
        knowledge.delete_document(doc["id"])


def test_search_finds_document_by_filename_alone_even_with_no_semantic_overlap():
    """Regression test: a bare, semantically-empty filename (a hex hash, the
    kind a phone gives a downloaded photo) used to return zero results even
    though the document obviously exists and is exactly what's being asked
    about — confirmed live against a real Aadhaar-card upload. A literal
    filename reference must always count as relevant, independent of
    embedding distance."""
    fake_fields = {"Name": "Jane Doe", "_document_type": "ID Card"}
    with patch("memory.knowledge.extraction.extract_structured_fields", return_value=dict(fake_fields)), patch(
        "memory.knowledge.ocr.available", return_value=True
    ), patch(
        "memory.knowledge.ocr.image_to_text", return_value="some OCR'd ID card text with no relation to the filename"
    ):
        doc = knowledge.ingest_document("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4.jpg", b"fake jpeg bytes")
    try:
        # The bare filename alone — no descriptive words, nothing for
        # semantic search to latch onto.
        results = knowledge.search("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4.jpg")
        assert len(results) == 1
        assert results[0]["filename"] == "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4.jpg"
        assert "| Name | Jane Doe |" in results[0]["text"]
    finally:
        knowledge.delete_document(doc["id"])


def test_search_filename_mention_does_not_suppress_genuinely_irrelevant_documents():
    """The filename exemption is per-document, not global — mentioning one
    document's filename shouldn't make an unrelated document's content
    suddenly pass the relevance bar too."""
    doc1 = knowledge.ingest_document("report.txt", b"Quarterly revenue grew by twelve percent this year.")
    doc2 = knowledge.ingest_document("unrelated.txt", b"My favorite pizza topping is pineapple.")
    try:
        results = knowledge.search("what does report.txt say about the weather forecast")
        filenames = {r["filename"] for r in results}
        assert "unrelated.txt" not in filenames
    finally:
        knowledge.delete_document(doc1["id"])
        knowledge.delete_document(doc2["id"])


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

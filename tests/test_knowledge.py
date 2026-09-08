"""
Tests for the knowledge base (memory/knowledge.py) and its tool wrapper.
Run with: pytest tests/
"""
import io
import re
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


def test_chunk_text_never_splits_a_word():
    """Regression test for a real, live-reproduced bug: the old pure
    character-count sliding window sliced straight through words at
    whatever character landed on the size boundary. Uploading a real PDF
    and searching for its own filename came back with chunks reading
    "nefits are shared widely..." (should start "benefits") and
    "...recognize different types of animals in im" (should end
    "...in images") — garbled at both ends, fed verbatim into chat answers
    by the same pipeline. Every chunk boundary must land on real
    whitespace, never inside a word."""
    text = (
        "Artificial Intelligence refers to the simulation of human intelligence in machines "
        "that are designed to think and act like humans. These intelligent systems can perform "
        "tasks such as learning, reasoning, problem-solving, perception, and language "
        "understanding. Machine Learning is a subset of AI that involves the development of "
        "algorithms and statistical models that enable computers to perform tasks without "
        "explicit instructions."
    )
    chunks = knowledge._chunk_text(text, size=120, overlap=20)
    assert len(chunks) > 1  # must actually exercise multiple chunk boundaries to be a real test

    real_words = set(re.findall(r"[A-Za-z']+", text))
    for chunk in chunks:
        for word in re.findall(r"[A-Za-z']+", chunk):
            assert word in real_words, f"chunk contains a word fragment not in the source text: {word!r}"

    # Rejoining the chunks (minus their overlap) must reproduce the
    # original words in order, with nothing dropped or corrupted.
    assert " ".join(chunks).split() == text.split() or all(
        w in text.split() for w in " ".join(chunks).split()
    )


def test_chunk_text_splits_one_giant_unbroken_word_with_a_hard_cut():
    """A single token with no whitespace at all (a long hash/URL) has no
    safe word boundary to break on — falls back to the old hard
    character cut rather than producing one giant oversized chunk. (Chunks
    legitimately overlap by design, so this doesn't assert exact
    reconstruction — just that nothing is corrupted or left oversized.)"""
    text = "x" * 2000
    chunks = knowledge._chunk_text(text, size=800, overlap=100)
    assert len(chunks) > 1
    assert all(len(c) <= 800 for c in chunks)
    assert all(set(c) <= {"x", " "} for c in chunks)
    assert len("".join(chunks).replace(" ", "")) >= len(text)


def test_search_returns_named_document_chunks_in_reading_order():
    """Regression test for a real, live-reproduced usability bug: searching
    a document by its own filename returned its chunks in raw embedding-
    similarity rank, not reading order — the first result shown was the
    document's *conclusion* (a late chunk), which reads as broken/random to
    a human. A query that explicitly names a document should show its
    content in the order it actually appears in."""
    # Long enough, and varied enough per paragraph, that embedding-distance
    # rank is very unlikely to already coincide with chunk order by chance.
    paragraphs = [
        "Chapter One covers the history of steam engines and early industrial machinery.",
        "Chapter Two discusses maritime navigation techniques used in the eighteenth century.",
        "Chapter Three examines agricultural crop rotation practices across different climates.",
        "Chapter Four analyzes medieval castle architecture and defensive fortifications.",
        "Chapter Five, the conclusion, reflects on how these historical themes connect today.",
    ]
    doc = knowledge.ingest_document("history_book.txt", ("\n".join(paragraphs)).encode())
    try:
        results = knowledge.search("history_book.txt", n_results=len(paragraphs))
        texts = [r["text"] for r in results]
        # Each paragraph's position among the results must match its
        # position in the source document.
        positions = [next(i for i, p in enumerate(paragraphs) if p in t) for t in texts]
        assert positions == sorted(positions)
    finally:
        knowledge.delete_document(doc["id"])


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


def test_ingest_persists_raw_text_but_list_documents_excludes_it():
    """raw_text is what makes reprocess_document() possible — it must
    actually be stored, but never sent in the bulk /api/knowledge listing
    (list_documents()), since the frontend never needs a document's full
    text on every page load / watched-folder poll."""
    doc = knowledge.ingest_document("full_text.txt", b"Some real document content for reprocessing later.")
    try:
        stored = structured.get_document(doc["id"])
        assert stored["raw_text"] == "Some real document content for reprocessing later."
        assert stored["content_hash"]  # non-empty

        listed = next(d for d in structured.list_documents() if d["id"] == doc["id"])
        assert "raw_text" not in listed
        assert "content_hash" not in listed
    finally:
        knowledge.delete_document(doc["id"])


def test_ingest_flags_an_exact_duplicate_without_blocking_it():
    """Duplicate detection is a non-blocking signal, not a rejection — a
    real re-upload (e.g. to pick up a pipeline improvement, exactly what
    this session needed to do) must still succeed."""
    doc1 = knowledge.ingest_document("first.txt", b"Identical content for duplicate detection.")
    try:
        assert doc1["duplicate_of"] is None
        doc2 = knowledge.ingest_document("second.txt", b"Identical content for duplicate detection.")
        try:
            assert doc2["duplicate_of"] == {"id": doc1["id"], "filename": "first.txt"}
        finally:
            knowledge.delete_document(doc2["id"])
    finally:
        knowledge.delete_document(doc1["id"])


def test_ingest_does_not_flag_different_content_as_duplicate():
    doc1 = knowledge.ingest_document("a.txt", b"First unique piece of content.")
    doc2 = knowledge.ingest_document("b.txt", b"Second, completely different piece of content.")
    try:
        assert doc1["duplicate_of"] is None
        assert doc2["duplicate_of"] is None
    finally:
        knowledge.delete_document(doc1["id"])
        knowledge.delete_document(doc2["id"])


def test_ingest_document_rejects_oversized_files():
    oversized = b"x" * (knowledge._MAX_UPLOAD_BYTES + 1)
    try:
        knowledge.ingest_document("huge.txt", oversized)
        assert False, "expected FileTooLarge"
    except knowledge.FileTooLarge as e:
        assert "too large" in str(e).lower()


def test_reprocess_document_reruns_extraction_from_stored_text():
    with patch("memory.knowledge.extraction.extract_structured_fields", return_value={"_document_type": "Note"}):
        doc = knowledge.ingest_document("reprocess_me.txt", b"Some content to extract fields from.")
    try:
        assert doc["doc_type"] == "Note"
        with patch(
            "memory.knowledge.extraction.extract_structured_fields",
            return_value={"Name": "Improved Extraction", "_document_type": "Better Type"},
        ):
            updated = knowledge.reprocess_document(doc["id"])
        assert updated["doc_type"] == "Better Type"
        assert updated["structured_data"] == {"Name": "Improved Extraction"}

        stored = structured.get_document(doc["id"])
        assert stored["doc_type"] == "Better Type"
        assert stored["structured_data"] == {"Name": "Improved Extraction"}
    finally:
        knowledge.delete_document(doc["id"])


def test_reprocess_document_returns_none_for_nonexistent_document():
    assert knowledge.reprocess_document(999999) is None


def test_reprocess_document_raises_for_a_document_with_no_stored_text():
    """Regression guard for documents that predate raw_text being stored —
    reprocessing has nothing to work from and must fail clearly rather than
    silently produce empty fields."""
    doc = knowledge.ingest_document("legacy.txt", b"Some content.")
    try:
        # Simulate a pre-migration row: raw_text wiped back to "".
        structured.update_document_structured_data(doc["id"], {}, "")
        with structured._connect() as conn:
            conn.execute("UPDATE documents SET raw_text = '' WHERE id = ?", (doc["id"],))
        try:
            knowledge.reprocess_document(doc["id"])
            assert False, "expected ValueError"
        except ValueError as e:
            assert "re-upload" in str(e).lower()
    finally:
        knowledge.delete_document(doc["id"])


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


def test_search_finds_document_by_filename_stem_without_the_extension():
    """Regression test for a real, live-reproduced bug: searching a real
    knowledge base containing "AIML.pdf" for just "AIML" — the natural,
    extension-less way anyone actually refers to a document, nobody types
    ".pdf" when asking about a file — returned "No matches" even though the
    query names the document as precisely as a full filename would, because
    the filename-mention bypass required the literal ".pdf" too. The
    document's own body text never uses the word "AIML" (it spells out
    "Artificial Intelligence and Machine Learning"), so this can only be
    fixed by the filename-stem match, not by keyword overlap. Uses a .txt
    file here (not a real .pdf) purely so ingestion doesn't need real PDF
    binary bytes — the filename-stem logic under test doesn't care about
    file type."""
    doc = knowledge.ingest_document(
        "AIML.txt",
        b"Fundamentals of Artificial Intelligence and Machine Learning. Understanding the Core Concepts.",
    )
    try:
        results = knowledge.search("AIML")
        assert any(r["filename"] == "AIML.txt" for r in results)
    finally:
        knowledge.delete_document(doc["id"])


def test_search_filename_stem_match_is_guarded_to_three_characters_minimum():
    """A trivially short stem must not match on any coincidental short
    fragment of the query — same anti-false-positive guard as the keyword-
    overlap floor above."""
    doc = knowledge.ingest_document("ab.txt", b"Completely unrelated content about gardening tips.")
    try:
        results = knowledge.search("please tell me about your favorite car brands")
        assert not any(r["filename"] == "ab.txt" for r in results)
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


def test_search_named_filename_does_not_leak_a_different_documents_content():
    """Regression test for a real bug found live: asking about one specific
    document by filename returned a *different* document's structured
    table instead, because both documents matched the unscoped semantic
    query and the other one happened to rank as the nearest embedding
    match. The one clean signal here — an explicit filename reference — must
    scope the search to that document alone, not just exempt it from the
    distance threshold while still mixing in whatever else ranks nearby."""
    fake_fields = {"Name": "Someone Else", "_document_type": "Aadhaar Card"}
    with patch("memory.knowledge.extraction.extract_structured_fields", return_value=dict(fake_fields)):
        doc1 = knowledge.ingest_document("clean_card.txt", b"a crisp, clean OCR read of an Aadhaar card")
    with patch("memory.knowledge.extraction.extract_structured_fields", return_value={}):
        doc2 = knowledge.ingest_document("garbled_card.txt", b"84 I W 1 L 9 4 1 8 1 Ii 4 1 U 3")
    try:
        results = knowledge.search("give me details of garbled_card.txt")
        filenames = {r["filename"] for r in results}
        assert filenames == {"garbled_card.txt"}
        assert "Someone Else" not in "".join(r["text"] for r in results)
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


def test_search_does_not_leak_ai_ml_content_into_an_unrelated_shopping_query():
    """Regression test for a real bug found live during the QA audit: with
    the knowledge base holding only an AI/ML-topic document, a completely
    unrelated Amazon shopping request ("Add Samsung Galaxy S25+ ... to
    cart") scored a LOWER embedding distance than the old 1.6 cutoff,
    so the ML document's content got injected as "relevant knowledge-base
    content" into agent/loop.py's outgoing prompt for a phone-shopping
    conversation — plausibly part of why the model then produced a
    confused, off-task reply instead of calling order_amazon. Reproduces
    that exact document topic and query."""
    doc = knowledge.ingest_document(
        "ai_notes.txt",
        b"Advancements in deep learning have led to significant improvements "
        b"in image and speech recognition. Common algorithms include linear "
        b"regression, decision trees, support vector machines, and "
        b"k-nearest neighbors used in machine learning and artificial "
        b"intelligence research.",
    )
    try:
        assert knowledge.search(
            "Add Samsung Galaxy S25+ 5G AI Smartphone (Silver Shadow, 12GB RAM, "
            "256GB Storage), 50MP Camera to cart."
        ) == []
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


def test_search_includes_page_number_for_pdf_chunks():
    """Regression coverage for a real gap: a chunk previously had no way to
    say *where* in a source document it came from. PDF chunks must now
    carry their real page number, in citations too."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)

    with patch("memory.knowledge.ocr.available", return_value=True), patch(
        "memory.knowledge.ocr.pdf_page_to_image_bytes", return_value=b"fake page image"
    ), patch(
        "memory.knowledge.ocr.image_to_text",
        side_effect=["Page one content about zebras.", "Page two content about giraffes."],
    ):
        doc = knowledge.ingest_document("two_page.pdf", buf.getvalue())
    try:
        zebra_results = knowledge.search("zebras")
        assert any(r["page"] == 1 and "zebras" in r["text"] for r in zebra_results)

        giraffe_results = knowledge.search("giraffes")
        assert any(r["page"] == 2 and "giraffes" in r["text"] for r in giraffe_results)

        formatted = knowledge.format_search_results(zebra_results)
        # The citation regex (/From '([^']+)':/) requires the filename to
        # sit immediately before the colon — the page number must come
        # AFTER it, never between the filename and the colon.
        assert "From 'two_page.pdf': (page 1)" in formatted
    finally:
        knowledge.delete_document(doc["id"])


def test_search_page_is_none_for_non_pdf_documents():
    doc = knowledge.ingest_document("plain.txt", b"Some plain text content with no page concept.")
    try:
        results = knowledge.search("plain text content")
        assert all(r["page"] is None for r in results)
        assert "(page" not in knowledge.format_search_results(results)
    finally:
        knowledge.delete_document(doc["id"])


def test_search_finds_exact_term_via_keyword_overlap_even_beyond_distance_threshold():
    """Hybrid search: the classic pure-semantic weak spot is an exact term
    (here, a made-up product code) that embeddings don't preserve
    precisely. A query sharing enough of the chunk's exact meaningful words
    must still surface it even if embedding distance alone wouldn't."""
    doc = knowledge.ingest_document(
        "part_specs.txt",
        b"The replacement part code is QRX-88214-ALPHA and must be ordered directly from the "
        b"manufacturer warehouse before installation.",
    )
    try:
        results = knowledge.search("What is the replacement part code QRX-88214-ALPHA?")
        assert any("QRX-88214-ALPHA" in r["text"] for r in results)
    finally:
        knowledge.delete_document(doc["id"])


def test_search_finds_a_single_distinctive_word_via_keyword_overlap():
    """Regression test for a real, live-reproduced bug: _keyword_overlap_hit
    had a hard floor of 2 matched words, which is unreachable for ANY
    single-word query no matter how exact the match — searching a real
    knowledge base containing "AIML.pdf" for the single word "AIML"
    returned "No matches" even though the acronym appears verbatim in the
    document, because a 1-word query can never contain 2 matched words."""
    doc = knowledge.ingest_document(
        "acronym_doc.txt",
        b"This course covers the fundamentals of AIML, a field combining statistics and computing.",
    )
    try:
        results = knowledge.search("AIML")
        assert any("AIML" in r["text"] for r in results)
    finally:
        knowledge.delete_document(doc["id"])


def test_search_still_requires_two_words_for_longer_queries():
    """The fix above must not weaken the original anti-false-positive
    intent for multi-word queries — a single coincidentally shared word
    still isn't enough on its own when the query has more words to check."""
    doc = knowledge.ingest_document(
        "unrelated_doc.txt",
        b"The quarterly report shows revenue increased significantly across all regions this year.",
    )
    try:
        # Shares only "report" with the document above — must not match on
        # that single coincidental word alone (distance threshold also
        # unlikely to pass for such an unrelated topic).
        results = knowledge.search("Please generate a status report for my homework assignment")
        assert not any("quarterly report" in r["text"] for r in results)
    finally:
        knowledge.delete_document(doc["id"])


def test_ingest_document_stores_and_retrieves_original_file():
    doc = knowledge.ingest_document("original.txt", b"Some real file bytes to store and retrieve.")
    try:
        original = knowledge.get_original_file(doc["id"])
        assert original is not None
        assert original["filename"] == "original.txt"
        assert original["content_type"] == "text/plain"
        assert original["data"] == b"Some real file bytes to store and retrieve."
    finally:
        knowledge.delete_document(doc["id"])


def test_get_original_file_returns_none_for_notes_and_urls():
    """Notes and URLs never have "original file bytes" — raw_text already
    is the whole original for those."""
    note = knowledge.ingest_note("A note title", "Some note body text.")
    try:
        assert knowledge.get_original_file(note["id"]) is None
    finally:
        knowledge.delete_document(note["id"])


def test_get_original_file_returns_none_for_nonexistent_document():
    assert knowledge.get_original_file(999999) is None


def test_get_document_reports_has_file_for_both_single_and_bulk_lookups():
    """Regression test for a real bug found live: structured.get_document
    (the single-document lookup the preview panel's "Download" button
    depends on) never computed has_file — only list_documents() did — so
    the download button silently never appeared for any real upload."""
    with_file = knowledge.ingest_document("has_file.txt", b"Some real file bytes.")
    without_file = knowledge.ingest_note("A note", "Notes never have an original file.")
    try:
        assert structured.get_document(with_file["id"])["has_file"] is True
        assert structured.get_document(without_file["id"])["has_file"] is False

        listed = {d["id"]: d for d in structured.list_documents()}
        assert listed[with_file["id"]]["has_file"] is True
        assert listed[without_file["id"]]["has_file"] is False
    finally:
        knowledge.delete_document(with_file["id"])
        knowledge.delete_document(without_file["id"])


def test_update_note_replaces_content_and_reembeds_under_the_same_id():
    note = knowledge.ingest_note("Original Title", "Original body about apples.", tags=["fruit"])
    try:
        assert any("apples" in r["text"] for r in knowledge.search("apples"))

        updated = knowledge.update_note(note["id"], "Updated Title", "Updated body about oranges.")
        assert updated["id"] == note["id"]  # same id — not a delete+recreate
        assert updated["filename"] == "Updated Title"
        assert updated["tags"] == ["fruit"]  # tags preserved when not explicitly changed

        assert knowledge.search("apples") == []  # old content is genuinely gone
        assert any("oranges" in r["text"] for r in knowledge.search("oranges"))

        stored = structured.get_document(note["id"])
        assert stored["filename"] == "Updated Title"
        assert stored["raw_text"] == "Updated body about oranges."
    finally:
        knowledge.delete_document(note["id"])


def test_update_note_can_change_tags():
    note = knowledge.ingest_note("Title", "Body text.", tags=["old"])
    try:
        updated = knowledge.update_note(note["id"], "Title", "Body text.", tags=["new"])
        assert updated["tags"] == ["new"]
        assert structured.get_document(note["id"])["tags"] == ["new"]
    finally:
        knowledge.delete_document(note["id"])


def test_update_note_returns_none_for_nonexistent_document():
    assert knowledge.update_note(999999, "Title", "Text") is None


def test_update_note_rejects_empty_title_or_text():
    note = knowledge.ingest_note("Title", "Text")
    try:
        for title, text in [("", "some text"), ("Title", "")]:
            try:
                knowledge.update_note(note["id"], title, text)
                assert False, "expected ValueError"
            except ValueError:
                pass
    finally:
        knowledge.delete_document(note["id"])


def test_summarize_document_returns_a_summary():
    doc = knowledge.ingest_document("article.txt", b"A long article about renewable energy trends.")
    try:
        fake_response = {"message": {"content": "Renewable energy is growing rapidly worldwide."}}
        with patch("ollama.chat", return_value=fake_response):
            result = knowledge.summarize_document(doc["id"])
        assert result["id"] == doc["id"]
        assert result["filename"] == "article.txt"
        assert result["summary"] == "Renewable energy is growing rapidly worldwide."
    finally:
        knowledge.delete_document(doc["id"])


def test_summarize_document_returns_none_for_nonexistent_document():
    assert knowledge.summarize_document(999999) is None


def test_summarize_document_raises_when_llm_call_fails():
    doc = knowledge.ingest_document("article2.txt", b"Some article content to summarize.")
    try:
        with patch("ollama.chat", side_effect=RuntimeError("connection refused")):
            try:
                knowledge.summarize_document(doc["id"])
                assert False, "expected ValueError"
            except ValueError as e:
                assert "unavailable" in str(e).lower() or "failed" in str(e).lower()
    finally:
        knowledge.delete_document(doc["id"])


def test_summarize_document_raises_for_a_document_with_no_stored_text():
    doc = knowledge.ingest_document("article3.txt", b"Some content.")
    try:
        with structured._connect() as conn:
            conn.execute("UPDATE documents SET raw_text = '' WHERE id = ?", (doc["id"],))
        try:
            knowledge.summarize_document(doc["id"])
            assert False, "expected ValueError"
        except ValueError as e:
            assert "re-upload" in str(e).lower()
    finally:
        knowledge.delete_document(doc["id"])

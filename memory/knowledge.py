"""
Knowledge base: a second, separate ChromaDB collection for documents the
user deliberately uploads (notes, PDFs), distinct from memory/vector.py's
conversation_memory. Kept apart on purpose — a past conversation snippet
and an uploaded document have different reliability characteristics, and
mixing "things that might be wrong" with "things that should be trusted"
is exactly the class of bug that once let a stale, wrong answer keep
resurfacing as if it were fact (see agent/loop.py's VOLATILE_TOOLS).
"""
import io

import requests

from memory import extraction, ocr, structured
from memory.vector import get_client

_collection = get_client().get_or_create_collection(name="knowledge_base")

# Hard cap on raw HTML processed per URL ingest — independent of whether the
# server's Content-Length header is honest, this bounds parsing cost against
# an accidentally (or maliciously) huge page.
_MAX_HTML_BYTES = 2_000_000


class UnsupportedFileType(Exception):
    pass


class OCRUnavailable(Exception):
    pass


_IMAGE_EXTENSIONS = ("png", "jpg", "jpeg")


def _extract_text(filename: str, data: bytes) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("txt", "md"):
        return data.decode("utf-8", errors="replace")
    if ext == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = []
        for i, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            if not text and ocr.available():
                # No real text layer on this page (a scanned/image-only
                # page) — render it and OCR it instead of leaving it blank.
                image_bytes = ocr.pdf_page_to_image_bytes(data, i)
                text = ocr.image_to_text(image_bytes)
            pages.append(text)
        return "\n".join(pages)
    if ext == "docx":
        from docx import Document

        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs)
    if ext in _IMAGE_EXTENSIONS:
        if not ocr.available():
            raise OCRUnavailable(
                "OCR isn't available on this server (missing torch/easyocr) — "
                "install the missing dependencies to upload images. See README.md's Setup section."
            )
        return ocr.image_to_text(data)
    raise UnsupportedFileType(
        f"Unsupported file type: .{ext or '?'} (supported: .txt, .md, .pdf, .docx, .png, .jpg, .jpeg)"
    )


def _extract_text_from_html(html: str) -> str:
    """Strip script/style/nav/boilerplate and return the visible text.
    Deliberately BeautifulSoup + a tag-removal heuristic rather than
    trafilatura or similar — matches this codebase's existing preference
    for small custom logic over heavier libraries (e.g. _chunk_text below,
    frontend/src/utils/text.ts's hand-rolled sentence splitter) for
    something this straightforward."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text(separator="\n").splitlines())
    return "\n".join(line for line in lines if line)


def _chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    """Simple sliding-window character split — no tokenizer dependency,
    matching this codebase's preference for small custom logic over heavy
    libraries (e.g. frontend/src/utils/text.ts's hand-rolled sentence
    splitter) for something this straightforward."""
    text = text.strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def _index_text(source: str, text: str, tags: list[str] | None = None, source_type: str = "manual") -> dict:
    """Chunk, embed, and record a piece of already-extracted text under
    `source` (a filename, URL, or note title — all just a label to the
    store). Also attempts structured field extraction (memory/extraction.py)
    — best-effort, never blocks or fails ingestion if it comes back empty.
    Returns metadata (id, filename, chunk_count, tags, doc_type,
    structured_data). Raises ValueError if there's no extractable text — the
    caller turns this into a 400."""
    chunks = _chunk_text(text)
    if not chunks:
        raise ValueError(f"No extractable text found in '{source}'")

    fields = extraction.extract_structured_fields(text)
    doc_type = fields.pop("_document_type", "") if fields else ""

    document_id = structured.add_document(source, len(chunks), tags, source_type, fields, doc_type)
    ids = [f"{document_id}_{i}" for i in range(len(chunks))]
    metadatas = [{"document_id": document_id, "filename": source, "chunk_index": i} for i in range(len(chunks))]
    _collection.add(documents=chunks, metadatas=metadatas, ids=ids)
    return {
        "id": document_id,
        "filename": source,
        "chunk_count": len(chunks),
        "tags": tags or [],
        "doc_type": doc_type,
        "structured_data": fields,
    }


def ingest_document(
    filename: str, data: bytes, tags: list[str] | None = None, source_type: str = "manual"
) -> dict:
    """Extract, chunk, and embed an uploaded file. Raises UnsupportedFileType,
    OCRUnavailable, or ValueError — the caller (server.py) turns these into
    a 400."""
    text = _extract_text(filename, data)
    return _index_text(filename, text, tags, source_type)


def ingest_url(url: str, tags: list[str] | None = None) -> dict:
    """Fetch a page, strip boilerplate, and chunk/embed the remaining text
    under the URL itself as its "filename". Raises requests.RequestException
    on a fetch failure, ValueError if no extractable text remains."""
    response = requests.get(url, timeout=10, headers={"User-Agent": "TEJAS-Assistant/1.0"})
    response.raise_for_status()
    text = _extract_text_from_html(response.text[:_MAX_HTML_BYTES])
    return _index_text(url, text, tags)


def ingest_note(title: str, text: str, tags: list[str] | None = None) -> dict:
    """Chunk and embed a manually-written note under its title as the
    "filename". Raises ValueError for an empty title or body — same
    handling as any other empty-content ingest."""
    title = title.strip()
    text = text.strip()
    if not title:
        raise ValueError("Note title cannot be empty")
    if not text:
        raise ValueError("Note text cannot be empty")
    return _index_text(title, text, tags)


def update_tags(document_id: int, tags: list[str]) -> bool:
    return structured.update_document_tags(document_id, tags)


def _table_cell(value) -> str:
    """Make a value safe to sit inside one Markdown table row. A raw
    newline or "|" inside a cell breaks table syntax outright (confirmed
    live: the model tried to work around a multi-line address by inserting
    literal "<br>" tags, which the chat UI doesn't render as HTML — it just
    showed up as literal text). memory/extraction.py's prompt now asks for
    single-line values up front; this is the defensive backstop for
    whatever gets through anyway. str()-cast defensively too: format="json"
    guarantees valid JSON, not that every value is already a plain string."""
    return str(value).replace("\r\n", ", ").replace("\n", ", ").replace("|", "/").strip()


# Literal marker row format_structured_table always emits — used to
# recognize its output again later (see is_structured_table below) without
# needing a separate flag threaded through every caller.
_TABLE_HEADER = "| Field | Value |"


def format_structured_table(doc: dict) -> str:
    """Render a document's extracted structured_data as a Markdown table —
    used by search() below so a question about a structured document (an ID
    card, an invoice, ...) gets back its actual fields instead of a raw OCR
    text chunk."""
    heading = f"**{doc['filename']}**" + (f" ({doc['doc_type']})" if doc.get("doc_type") else "")
    rows = [f"| {_table_cell(k)} | {_table_cell(v)} |" for k, v in doc["structured_data"].items()]
    return "\n".join([heading, "", _TABLE_HEADER, "|---|---|", *rows])


def is_structured_table(text: str) -> bool:
    """Whether a search() result's text is a format_structured_table()
    output (an exact document field table) rather than a raw content chunk
    — see agent/loop.py's chat_streaming, which appends these verbatim
    after the model's reply instead of trusting the model to retype them
    correctly. Confirmed live, repeatedly: even with an explicit "present
    this table AS-IS" system-prompt instruction, a 7B local model would
    still "helpfully" reformat or guess at a cleaner-looking value for an
    illegible OCR field (e.g. inventing a plausible date that appears
    nowhere in the actual source) — the same category of instruction-
    following unreliability already seen with tool-calling, so the fix is
    the same: stop asking the model to reproduce it faithfully, and instead
    guarantee fidelity by not routing it through the model's own generation
    at all."""
    return _TABLE_HEADER in text


# ChromaDB L2 distance cutoff, calibrated empirically against this
# collection's embedding function: genuinely relevant matches (even loosely
# worded) land under ~1.35, unrelated content starts around ~1.85+. Without
# this, query() always returns its n_results nearest neighbors regardless of
# whether anything is actually relevant — confirmed as a real problem when a
# single noisy/garbled chunk (e.g. OCR output full of misreads) embeds to a
# spuriously "central" vector that looks closer to unrelated queries than
# any genuinely relevant document does, drowning out real matches and
# surfacing content that has nothing to do with what was asked.
_MAX_RELEVANT_DISTANCE = 1.6


def _filenames_mentioned_in(query: str) -> set[str]:
    """Document filenames that appear verbatim (case-insensitive) in the
    query — a literal reference like "tell me about report.pdf" or "what's
    in 0550103960be78c2214de67da34304c0.jpg" is a much stronger, unambiguous
    relevance signal than embedding distance, which can fail entirely for a
    filename with little semantic content of its own (a hex-hash-named
    photo, for instance) — confirmed live: querying with just that filename
    ranked the right document 1st but still landed just over the distance
    threshold, since a hash string barely resembles the document's actual
    (OCR'd) content in embedding space. Only checks the filename-in-query
    direction, not the reverse — matching on "does the query contain this
    filename" is precise; the reverse ("does this filename contain the
    query") would trigger on any short/generic query fragment."""
    query_lower = query.lower()
    return {doc["filename"] for doc in structured.list_documents() if doc["filename"].lower() in query_lower}


def search(query: str, n_results: int = 5) -> list[dict]:
    """Retrieve the most semantically relevant chunks across all uploaded
    documents, excluding anything too far from the query to actually be
    relevant — unless the query directly names the document by filename,
    which always counts as relevant regardless of embedding distance (see
    _filenames_mentioned_in). Returns [{filename, text}, ...].

    When a matched chunk belongs to a document with extracted structured
    data (an ID card, an invoice, ...), its raw chunk text is swapped for
    the document's full field table instead — a question about a structured
    document should get its actual fields, not a raw (possibly OCR-garbled)
    text fragment. Deduplicated per document so a multi-chunk structured
    document doesn't repeat its table once per matching chunk."""
    if _collection.count() == 0:
        return []
    named_filenames = _filenames_mentioned_in(query)
    results = _collection.query(query_texts=[query], n_results=min(n_results, _collection.count()))
    docs = results["documents"][0] if results["documents"] else []
    metas = results["metadatas"][0] if results["metadatas"] else []
    dists = results["distances"][0] if results["distances"] else []

    output = []
    seen_structured_doc_ids = set()
    for d, m, dist in zip(docs, metas, dists):
        filename = m.get("filename", "unknown")
        if dist > _MAX_RELEVANT_DISTANCE and filename not in named_filenames:
            continue
        document_id = m.get("document_id")
        doc_row = structured.get_document(document_id) if document_id is not None else None

        if doc_row and doc_row["structured_data"]:
            if document_id in seen_structured_doc_ids:
                continue
            seen_structured_doc_ids.add(document_id)
            output.append({"filename": filename, "text": format_structured_table(doc_row)})
        else:
            output.append({"filename": filename, "text": d})
    return output


# Bound on how many documents the ambient listing below names individually
# — cheap for a realistic personal knowledge base (tens of documents), but
# unbounded would let a very large library bloat every single turn's prompt
# just to answer a question about only one of them.
_MAX_LISTED_DOCUMENTS = 30


def document_listing() -> str:
    """A compact one-line-per-document summary (filename + type), for
    agent/loop.py's per-turn context — gives the model ambient awareness of
    what exists so it can correctly answer "what's in my knowledge base" /
    "what documents do I have", which search() alone can't: those are
    listing questions, not content queries, so nothing in any one chunk's
    text says "here is the complete list." Returns "" when the knowledge
    base is empty, so the caller can skip the section entirely."""
    docs = structured.list_documents()
    if not docs:
        return ""
    lines = [f"- {d['filename']}" + (f" ({d['doc_type']})" if d.get("doc_type") else "") for d in docs[:_MAX_LISTED_DOCUMENTS]]
    if len(docs) > _MAX_LISTED_DOCUMENTS:
        lines.append(f"- ...and {len(docs) - _MAX_LISTED_DOCUMENTS} more")
    return "\n".join(lines)


def format_search_results(results: list[dict]) -> str:
    """Format search() results into the single plain-text shape used
    everywhere a caller needs one string: tools/knowledge_tool.py's explicit
    tool call, and agent/loop.py's proactive per-turn injection. Shared so
    both paths produce byte-identical output — frontend/src/hooks/
    useAssistantSocket.ts's citation extraction depends on this exact
    "From 'filename': text" shape regardless of which path produced it."""
    if not results:
        return "No relevant documents found in the knowledge base."
    return "\n\n".join(f"From '{r['filename']}': {r['text']}" for r in results)


def delete_document(document_id: int) -> bool:
    """Delete a document's SQLite record and all its Chroma chunks. SQLite
    is the source of truth for existence — the Chroma cleanup is only
    attempted once we know the document actually existed."""
    deleted = structured.delete_document(document_id)
    if deleted:
        _collection.delete(where={"document_id": document_id})
    return deleted

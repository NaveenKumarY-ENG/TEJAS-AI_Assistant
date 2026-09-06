"""
Knowledge base: a second, separate ChromaDB collection for documents the
user deliberately uploads (notes, PDFs), distinct from memory/vector.py's
conversation_memory. Kept apart on purpose — a past conversation snippet
and an uploaded document have different reliability characteristics, and
mixing "things that might be wrong" with "things that should be trusted"
is exactly the class of bug that once let a stale, wrong answer keep
resurfacing as if it were fact (see agent/loop.py's VOLATILE_TOOLS).
"""
import hashlib
import io
import logging
import re

import requests

from config import config
from memory import extraction, ocr, structured
from memory.vector import get_client

logger = logging.getLogger("assistant.knowledge")

_collection = get_client().get_or_create_collection(name="knowledge_base")

# Hard cap on raw HTML processed per URL ingest — independent of whether the
# server's Content-Length header is honest, this bounds parsing cost against
# an accidentally (or maliciously) huge page.
_MAX_HTML_BYTES = 2_000_000

# Hard cap on an uploaded file's raw size — previously unbounded entirely: a
# huge accidental upload (or a deliberately hostile one) would tie up a
# worker thread extracting/OCR-ing/embedding it with no timeout and no
# feedback beyond a spinner. 25MB comfortably covers a real personal
# document (even a scanned, image-heavy PDF of a few dozen pages) while
# keeping a worst-case upload's processing time bounded.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class UnsupportedFileType(Exception):
    pass


class OCRUnavailable(Exception):
    pass


class FileTooLarge(Exception):
    pass


_IMAGE_EXTENSIONS = ("png", "jpg", "jpeg")


def _extract_pdf_pages(data: bytes) -> list[str]:
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
    return pages


def _extract_text(filename: str, data: bytes) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("txt", "md"):
        return data.decode("utf-8", errors="replace")
    if ext == "pdf":
        return "\n".join(_extract_pdf_pages(data))
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


def _extract_pages(filename: str, data: bytes) -> list[tuple[int | None, str]]:
    """Page-tagged extraction — only PDFs have a real page concept; every
    other supported type comes back as a single untagged (None) page. Used
    by ingest_document so each chunk's metadata (and therefore chat
    citations, via search()/format_search_results) can say "page N" — a
    chunk previously had no way to point back to *where* in the source
    document it came from. Trade-off: chunking happens per-page rather
    than across the whole joined document, so a paragraph that spans a
    page break gets split there even when there'd be room to keep it
    together — an acceptable cost for a citation that's actually correct,
    and the existing overlap mechanism still gives each page's first/last
    chunk some neighboring context."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pdf":
        return [(i + 1, text) for i, text in enumerate(_extract_pdf_pages(data))]
    return [(None, _extract_text(filename, data))]


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


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _split_into_sentences(text: str) -> list[str]:
    """Best-effort sentence splitter for finding safe chunk-boundary points
    — not linguistically precise, matching this codebase's preference for
    small custom logic over a heavy NLP library (e.g. frontend/src/utils/
    text.ts's hand-rolled sentence splitter) for something this
    straightforward. Splits on newlines first: a PDF's own line breaks
    (including ones that land mid-sentence, from pypdf's layout-based
    extraction) are still a safe place to break for chunk-packing purposes,
    since _chunk_text below rejoins consecutive segments with a single
    space regardless of where they came from."""
    parts = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        parts.extend(s.strip() for s in _SENTENCE_SPLIT_RE.split(line) if s.strip())
    return parts


def _split_long_segment(segment: str, size: int) -> list[str]:
    """Break a single sentence/line too long to fit in one chunk at the
    last whitespace before `size` chars — never mid-word. Falls back to a
    hard character cut only when the segment has no whitespace at all
    within the window (e.g. one giant unbroken token), matching the old
    behavior for that pathological case."""
    pieces = []
    while len(segment) > size:
        cut = segment.rfind(" ", 0, size)
        if cut <= 0:
            cut = size  # no whitespace to break on — hard cut, same as before
        pieces.append(segment[:cut].strip())
        segment = segment[cut:].strip()
    if segment:
        pieces.append(segment)
    return pieces


def _chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    """Pack sentences/lines into ~`size`-character chunks, breaking only at
    sentence or word boundaries — never mid-word. The previous
    implementation was a pure character-count sliding window (text[start:
    start+size]) with zero regard for word boundaries: confirmed live as a
    real, high-impact bug — real chunks came back reading "nefits are
    shared..." (should start "benefits") and "...recognize different types
    of animals in im" (should end "...in images"), both in the Knowledge
    Base's own search results and in every chat answer the RAG pipeline
    built from them, since search() feeds these same chunks to the model.
    `overlap` chars of the previous chunk's tail are carried into the next
    chunk for retrieval continuity, snapped forward to a word boundary so
    the overlap itself doesn't start mid-word either."""
    text = text.strip()
    if not text:
        return []

    segments = []
    for sentence in _split_into_sentences(text):
        segments.extend(_split_long_segment(sentence, size))

    chunks = []
    current = ""
    for segment in segments:
        candidate = f"{current} {segment}".strip() if current else segment
        if not current or len(candidate) <= size:
            current = candidate
            continue
        chunks.append(current)
        tail = current[-overlap:]
        space = tail.find(" ")
        if space != -1:
            tail = tail[space + 1 :]  # drop the partial word at the start of the overlap window
        seed = f"{tail} {segment}".strip() if tail else segment
        # A carried-over tail can occasionally push the seed itself past
        # `size` (e.g. a segment that's already near the size limit) — drop
        # the overlap rather than start the next chunk already oversized.
        current = seed if len(seed) <= size else segment
    if current:
        chunks.append(current)
    return chunks


def _index_pages(
    source: str, pages: list[tuple[int | None, str]], tags: list[str] | None = None, source_type: str = "manual"
) -> dict:
    """Chunk, embed, and record already-extracted, page-tagged text under
    `source` (a filename, URL, or note title — all just a label to the
    store). `pages` is a list of (page_number_or_None, text) — most callers
    pass a single (None, text) entry; ingest_document passes one entry per
    PDF page (see _extract_pages) so each resulting chunk can carry the
    page it came from. Also attempts structured field extraction
    (memory/extraction.py) against the full joined text — best-effort,
    never blocks or fails ingestion if it comes back empty.

    The full text is persisted alongside the chunks (structured.add_document's
    raw_text) — previously it existed only transiently in memory during
    ingestion, which meant reprocess_document()/summarize_document() below
    couldn't exist at all, and any chunking-quality fix could only ever
    apply to documents uploaded *after* it shipped. Returns metadata (id,
    filename, chunk_count, tags, doc_type, structured_data, and
    duplicate_of — the existing document's {id, filename} if this exact
    text was already ingested, or None). Raises ValueError if there's no
    extractable text — the caller turns this into a 400.

    Duplicate detection is deliberately a non-blocking signal, not a
    rejection: ingestion still proceeds either way. A hard block would have
    gotten in the way of a perfectly legitimate re-upload (e.g. re-ingesting
    a document to pick up a pipeline improvement, exactly what this session
    needed to do for a real document right after fixing _chunk_text) — the
    caller/UI decides what to do with the warning."""
    chunks: list[tuple[str, int]] = []  # (chunk_text, page_or_0)
    for page_num, page_text in pages:
        for c in _chunk_text(page_text):
            chunks.append((c, page_num or 0))
    if not chunks:
        raise ValueError(f"No extractable text found in '{source}'")

    full_text = "\n".join(t for _, t in pages).strip()
    content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
    existing = structured.find_document_by_hash(content_hash)

    fields = extraction.extract_structured_fields(full_text)
    doc_type = fields.pop("_document_type", "") if fields else ""

    document_id = structured.add_document(
        source, len(chunks), tags, source_type, fields, doc_type, raw_text=full_text, content_hash=content_hash
    )
    ids = [f"{document_id}_{i}" for i in range(len(chunks))]
    metadatas = [
        {"document_id": document_id, "filename": source, "chunk_index": i, "page": page}
        for i, (_, page) in enumerate(chunks)
    ]
    _collection.add(documents=[c for c, _ in chunks], metadatas=metadatas, ids=ids)
    return {
        "id": document_id,
        "filename": source,
        "chunk_count": len(chunks),
        "tags": tags or [],
        "doc_type": doc_type,
        "structured_data": fields,
        "duplicate_of": {"id": existing["id"], "filename": existing["filename"]} if existing else None,
    }


def ingest_document(
    filename: str, data: bytes, tags: list[str] | None = None, source_type: str = "manual"
) -> dict:
    """Extract, chunk, and embed an uploaded file — and keep the original
    bytes (structured.save_document_file) so "download the original" and
    reprocessing survive a future pipeline change without needing a
    re-upload. Raises UnsupportedFileType, OCRUnavailable, FileTooLarge, or
    ValueError — the caller (server.py) turns these into a 400."""
    if len(data) > _MAX_UPLOAD_BYTES:
        raise FileTooLarge(
            f"File is too large ({len(data) / 1_048_576:.1f}MB) — the limit is "
            f"{_MAX_UPLOAD_BYTES // 1_048_576}MB per upload."
        )
    pages = _extract_pages(filename, data)
    doc = _index_pages(filename, pages, tags, source_type)
    structured.save_document_file(doc["id"], _content_type_for(filename), data)
    return doc


_CONTENT_TYPES = {
    "txt": "text/plain",
    "md": "text/markdown",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}


def _content_type_for(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _CONTENT_TYPES.get(ext, "application/octet-stream")


def get_original_file(document_id: int) -> dict | None:
    """The bytes ingest_document stored, for a "download original" action.
    Returns {"filename", "content_type", "data"}, or None if the document
    doesn't exist or has no stored file (notes/URLs never have one — there
    is no "original file" for text typed directly into the app, or a page
    fetched from the web; raw_text already *is* the full original for
    those)."""
    doc = structured.get_document(document_id)
    if doc is None:
        return None
    file_row = structured.get_document_file(document_id)
    if file_row is None:
        return None
    return {"filename": doc["filename"], "content_type": file_row["content_type"], "data": file_row["data"]}


def reprocess_document(document_id: int) -> dict | None:
    """Re-run structured field extraction (memory/extraction.py) against a
    document's stored text, without needing the original file re-uploaded —
    useful after improving the extraction prompt, or if the first pass
    missed something. Returns the updated {id, filename, doc_type,
    structured_data}, or None if the document doesn't exist. Raises
    ValueError if the document predates raw_text being stored (nothing to
    reprocess from) — the caller turns this into a 400 telling the user to
    re-upload."""
    doc = structured.get_document(document_id)
    if doc is None:
        return None
    if not doc["raw_text"]:
        raise ValueError(
            "This document was uploaded before reprocessing was supported and has no stored "
            "content to re-extract from — re-upload it to enable this."
        )
    fields = extraction.extract_structured_fields(doc["raw_text"])
    doc_type = fields.pop("_document_type", "") if fields else ""
    structured.update_document_structured_data(document_id, fields, doc_type)
    return {"id": document_id, "filename": doc["filename"], "doc_type": doc_type, "structured_data": fields}


# ID cards/invoices are short; a summarization target is the opposite case
# (an article, a report, a chapter) so this gets a much larger budget than
# extraction.py's _MAX_INPUT_CHARS (6000). Still a single bounded pass, not
# real map-reduce summarization across chunks — a genuinely book-length
# document gets a summary of its first ~15000 characters, not the whole
# thing. Real full-document summarization (map-reduce over chunks) is
# worthwhile future work, not attempted here.
_MAX_SUMMARIZE_INPUT_CHARS = 15000

_SUMMARIZE_PROMPT = (
    "Summarize the following document in 3-5 concise, plain-prose sentences covering its main "
    "points. Do not add commentary, headers, or bullet points — just the summary sentences.\n\n"
    "---\n{text}\n---"
)


def summarize_document(document_id: int) -> dict | None:
    """One-pass summarization of a document's stored text via the local
    LLM — deliberately always local (same reasoning as
    memory/extraction.py: direct `ollama.chat`, bypassing
    agent/llm_client.py's provider abstraction) regardless of which chat
    provider is active. Returns {id, filename, summary}, or None if the
    document doesn't exist. Raises ValueError if there's no stored text, or
    if the local model call itself fails — unlike extraction (best-effort,
    runs silently on every upload), this is an explicit, on-demand user
    action, so a real error surfaced to the user beats silently returning
    nothing."""
    doc = structured.get_document(document_id)
    if doc is None:
        return None
    if not doc["raw_text"]:
        raise ValueError(
            "This document has no stored content to summarize — re-upload it to enable this."
        )
    import ollama

    try:
        response = ollama.chat(
            model=config.ollama_model,
            messages=[
                {
                    "role": "user",
                    "content": _SUMMARIZE_PROMPT.format(text=doc["raw_text"][:_MAX_SUMMARIZE_INPUT_CHARS]),
                }
            ],
            options={"temperature": 0.3},
        )
        summary = response["message"]["content"].strip()
    except Exception:
        logger.exception("Document summarization failed")
        raise ValueError("Summarization failed — the local model may be unavailable. Try again in a moment.")
    if not summary:
        raise ValueError("Summarization returned nothing — try again.")
    return {"id": document_id, "filename": doc["filename"], "summary": summary}


def ingest_url(url: str, tags: list[str] | None = None) -> dict:
    """Fetch a page, strip boilerplate, and chunk/embed the remaining text
    under the URL itself as its "filename". Raises requests.RequestException
    on a fetch failure, ValueError if no extractable text remains."""
    response = requests.get(url, timeout=10, headers={"User-Agent": "TEJAS-Assistant/1.0"})
    response.raise_for_status()
    text = _extract_text_from_html(response.text[:_MAX_HTML_BYTES])
    return _index_pages(url, [(None, text)], tags)


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
    return _index_pages(title, [(None, text)], tags)


def update_note(document_id: int, title: str, text: str, tags: list[str] | None = None) -> dict | None:
    """Replace a note's title/text in place, re-chunking and re-embedding
    under the SAME document id — editing a note previously meant delete +
    recreate, which lost its position in the list and forced re-tagging
    from scratch. Returns the updated {id, filename, chunk_count, tags,
    doc_type, structured_data}, or None if the document doesn't exist.
    Raises ValueError for an empty title/text, same as ingest_note.
    Works against any document by id, not just ones actually created via
    ingest_note — the Notes/Documents split is a frontend filename-shape
    heuristic (see KnowledgePanel.tsx's isNoteSource), not a backend
    distinction, so there's nothing meaningful to enforce here."""
    title = title.strip()
    text = text.strip()
    if not title:
        raise ValueError("Note title cannot be empty")
    if not text:
        raise ValueError("Note text cannot be empty")

    doc = structured.get_document(document_id)
    if doc is None:
        return None

    chunks = _chunk_text(text)
    if not chunks:
        raise ValueError("No extractable text found in the note")

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    fields = extraction.extract_structured_fields(text)
    doc_type = fields.pop("_document_type", "") if fields else ""

    _collection.delete(where={"document_id": document_id})
    ids = [f"{document_id}_{i}" for i in range(len(chunks))]
    metadatas = [
        {"document_id": document_id, "filename": title, "chunk_index": i, "page": 0} for i in range(len(chunks))
    ]
    _collection.add(documents=chunks, metadatas=metadatas, ids=ids)

    final_tags = tags if tags is not None else doc["tags"]
    structured.update_document_content(document_id, title, len(chunks), text, content_hash, fields, doc_type)
    if tags is not None and tags != doc["tags"]:
        structured.update_document_tags(document_id, final_tags)

    return {
        "id": document_id,
        "filename": title,
        "chunk_count": len(chunks),
        "tags": final_tags,
        "doc_type": doc_type,
        "structured_data": fields,
    }


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


# ChromaDB L2 distance cutoff. Without this, query() always returns its
# n_results nearest neighbors regardless of whether anything is actually
# relevant — confirmed as a real problem when a single noisy/garbled chunk
# (e.g. OCR output full of misreads) embeds to a spuriously "central" vector
# that looks closer to unrelated queries than any genuinely relevant
# document does, drowning out real matches and surfacing content that has
# nothing to do with what was asked.
#
# Re-calibrated 2026-08-31 (QA audit) after finding the previous cutoff
# (1.6, said to be based on "unrelated content starts around ~1.85+") no
# longer matched this collection's actual behavior — a real, live-
# reproduced bug: a completely unrelated Amazon shopping request ("Add
# Samsung Galaxy S25+ ... to cart") scored a *lower* distance (1.417) than
# this constant, so an ML/AI document's content about "deep learning"
# and "image and speech recognition" was injected as "relevant knowledge-
# base content" into a phone-shopping conversation — plausibly part of why
# the model then went on to give a confused, off-task response. Freshly
# measured on the actual current collection: genuinely relevant queries
# land at 0.61-0.73, genuinely unrelated ones at 1.64-1.89 — a threshold in
# that gap is what the number below now reflects. This value is a property
# of the CURRENT document set (fewer/different documents shift the "nearest
# neighbor" floor), not a universal constant — worth re-checking with the
# same kind of direct query-distance measurement if it starts looking wrong
# again as the knowledge base's contents change.
_MAX_RELEVANT_DISTANCE = 1.2

# A short, generic word list to exclude from keyword-overlap matching below
# — otherwise "what does the document say about" would itself count as
# overlapping words against nearly anything.
_STOPWORDS = frozenset(
    "the a an is are was were in on at to for of and or what how does do did this that with from "
    "about tell me my you your it its be can could would should will has have had which who whom".split()
)

# A second, independent relevance signal alongside embedding distance —
# closes the classic pure-semantic-search weak spot: an exact term (an
# invoice number, an error code, an acronym, a proper noun) that embeddings
# compress into vague vector space rather than preserving precisely. A
# chunk beyond _MAX_RELEVANT_DISTANCE still counts as relevant if MOST of
# the query's meaningful words appear in it verbatim — both a high ratio
# (not just one word coincidentally in common) and at least two matched
# words are required, since a single generic overlap isn't a strong enough
# signal on its own. Deliberately conservative: this only ever adds matches
# among the n_results candidates embedding search already surfaced, never
# searches the whole corpus by keyword.
_MIN_KEYWORD_OVERLAP_RATIO = 0.6
_MIN_KEYWORD_OVERLAP_COUNT = 2
_WORD_RE = re.compile(r"[a-z0-9']{3,}")


def _significant_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


def _keyword_overlap_hit(query_words: set[str], chunk_text: str) -> bool:
    if not query_words:
        return False
    matched = query_words & _significant_words(chunk_text)
    return len(matched) >= _MIN_KEYWORD_OVERLAP_COUNT and len(matched) / len(query_words) >= _MIN_KEYWORD_OVERLAP_RATIO


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
    relevant — unless the query directly names the document by filename
    (see _filenames_mentioned_in) or shares enough exact meaningful words
    with the chunk (see _keyword_overlap_hit), either of which always
    counts as relevant regardless of embedding distance. Returns
    [{filename, text, page}, ...] — page is None for anything without a
    real page concept (everything except a PDF chunk).

    When a matched chunk belongs to a document with extracted structured
    data (an ID card, an invoice, ...), its raw chunk text is swapped for
    the document's full field table instead — a question about a structured
    document should get its actual fields, not a raw (possibly OCR-garbled)
    text fragment. Deduplicated per document so a multi-chunk structured
    document doesn't repeat its table once per matching chunk."""
    if _collection.count() == 0:
        return []
    named_filenames = _filenames_mentioned_in(query)
    query_words = _significant_words(query)
    query_kwargs = {"query_texts": [query], "n_results": min(n_results, _collection.count())}
    if named_filenames:
        # An explicit filename reference scopes the search to just that
        # document, via a metadata filter rather than embedding distance —
        # confirmed live as a real bug otherwise: asking about a specific
        # image with little/no extractable text of its own (a failed OCR
        # read) still returned a *different*, merely-similar-looking
        # document's chunks as the nearest embedding matches, and that
        # document's structured field table (someone else's Aadhaar Card
        # details) got shown as if it were the requested file's. A metadata
        # filter guarantees the named document's own chunks are what's
        # actually retrieved, instead of hoping they happen to rank in the
        # unscoped top-n_results on embedding distance alone.
        query_kwargs["where"] = {"filename": {"$in": list(named_filenames)}}
    results = _collection.query(**query_kwargs)
    docs = results["documents"][0] if results["documents"] else []
    metas = results["metadatas"][0] if results["metadatas"] else []
    dists = results["distances"][0] if results["distances"] else []
    rows = list(zip(docs, metas, dists))

    if named_filenames:
        # The `where` filter above guarantees every row here belongs to a
        # named document, so this is safe to sort unconditionally. Reading
        # order beats raw similarity rank for a query that names a specific
        # document — confirmed live as a real usability bug: searching a
        # document by its own filename returned its *conclusion* (a late
        # chunk) first and an early chunk fourth, an order that reads as
        # arbitrary/broken to a human skimming results even though every
        # individual chunk was itself a legitimate match. Chunks from
        # documents that weren't named stay in similarity rank — there's no
        # equally strong ordering signal for those.
        rows.sort(key=lambda row: row[1].get("chunk_index", 0))

    output = []
    seen_structured_doc_ids = set()
    for d, m, dist in rows:
        filename = m.get("filename", "unknown")
        relevant = (
            dist <= _MAX_RELEVANT_DISTANCE or filename in named_filenames or _keyword_overlap_hit(query_words, d)
        )
        if not relevant:
            continue
        document_id = m.get("document_id")
        doc_row = structured.get_document(document_id) if document_id is not None else None
        page = m.get("page") or None  # 0 (the "no page" sentinel) -> None

        if doc_row and doc_row["structured_data"]:
            if document_id in seen_structured_doc_ids:
                continue
            seen_structured_doc_ids.add(document_id)
            output.append({"filename": filename, "text": format_structured_table(doc_row), "page": None})
        else:
            output.append({"filename": filename, "text": d, "page": page})
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
    "From 'filename':" PREFIX (its regex is /From '([^']+)':/ — the
    filename must sit immediately before the colon with nothing in
    between), so a page number, when known, is appended right AFTER the
    colon instead: "From 'filename': (page N) text..."."""
    if not results:
        return "No relevant documents found in the knowledge base."
    lines = []
    for r in results:
        page_prefix = f"(page {r['page']}) " if r.get("page") else ""
        lines.append(f"From '{r['filename']}': {page_prefix}{r['text']}")
    return "\n\n".join(lines)


def delete_document(document_id: int) -> bool:
    """Delete a document's SQLite record and all its Chroma chunks. SQLite
    is the source of truth for existence — the Chroma cleanup is only
    attempted once we know the document actually existed."""
    deleted = structured.delete_document(document_id)
    if deleted:
        _collection.delete(where={"document_id": document_id})
    return deleted

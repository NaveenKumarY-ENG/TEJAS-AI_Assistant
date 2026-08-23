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

from memory import ocr, structured
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
    store). Returns metadata (id, filename, chunk_count, tags). Raises
    ValueError if there's no extractable text — the caller turns this into
    a 400."""
    chunks = _chunk_text(text)
    if not chunks:
        raise ValueError(f"No extractable text found in '{source}'")

    document_id = structured.add_document(source, len(chunks), tags, source_type)
    ids = [f"{document_id}_{i}" for i in range(len(chunks))]
    metadatas = [{"document_id": document_id, "filename": source, "chunk_index": i} for i in range(len(chunks))]
    _collection.add(documents=chunks, metadatas=metadatas, ids=ids)
    return {"id": document_id, "filename": source, "chunk_count": len(chunks), "tags": tags or []}


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


def search(query: str, n_results: int = 5) -> list[dict]:
    """Retrieve the most semantically relevant chunks across all uploaded
    documents, excluding anything too far from the query to actually be
    relevant. Returns [{filename, text}, ...]."""
    if _collection.count() == 0:
        return []
    results = _collection.query(query_texts=[query], n_results=min(n_results, _collection.count()))
    docs = results["documents"][0] if results["documents"] else []
    metas = results["metadatas"][0] if results["metadatas"] else []
    dists = results["distances"][0] if results["distances"] else []
    return [
        {"filename": m.get("filename", "unknown"), "text": d}
        for d, m, dist in zip(docs, metas, dists)
        if dist <= _MAX_RELEVANT_DISTANCE
    ]


def delete_document(document_id: int) -> bool:
    """Delete a document's SQLite record and all its Chroma chunks. SQLite
    is the source of truth for existence — the Chroma cleanup is only
    attempted once we know the document actually existed."""
    deleted = structured.delete_document(document_id)
    if deleted:
        _collection.delete(where={"document_id": document_id})
    return deleted

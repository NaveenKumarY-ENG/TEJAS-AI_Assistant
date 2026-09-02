"""OCR for the knowledge base: EasyOCR, GPU-accelerated when CUDA is
available (falls back to CPU otherwise) — reuses the exact torch/CUDA
install neural TTS already requires (see agent/tts.py).

Same lazy-singleton, fail-soft shape as agent/tts.py: the reader loads on
first use (a few seconds, plus a one-time ~100MB weights download), and any
missing dependency or runtime error is caught rather than crashing the
request — memory/knowledge.py raises a clear OCRUnavailable instead of
silently producing an empty document when this reports itself unavailable.
"""
import io
import logging
import threading

from config import config

logger = logging.getLogger("assistant.ocr")

_reader = None
_reader_lock = threading.Lock()
_availability_cache: bool | None = None


def _get_reader():
    global _reader
    if _reader is None:
        # Double-checked locking: without this, two concurrent first callers
        # would both pass the outer None-check and each construct their own
        # Reader — doubling GPU memory transiently, which can OOM on a small
        # card. This same lock is reused in image_to_text() below to also
        # serialize actual inference calls, not just this one-time
        # construction — the exact same race condition (and fix) already
        # found once in agent/tts.py's _get_pipeline()/synthesize().
        with _reader_lock:
            if _reader is None:
                import torch
                import easyocr

                gpu = torch.cuda.is_available() and config.ocr_device != "cpu"
                logger.info(
                    "Loading EasyOCR reader on %s (first use only, may download weights)...",
                    "cuda" if gpu else "cpu",
                )
                # English + Hindi: many real-world documents worth OCR-ing
                # here (Aadhaar cards especially) are bilingual, with English
                # field values sitting right next to Devanagari labels. An
                # English-only reader doesn't skip that Hindi text — it
                # still tries to force those glyphs into English character
                # predictions, and that garbage bleeds into the adjacent
                # real text too (confirmed live: an Aadhaar number came back
                # as "AASHN XMLATAATnR64", an address as "SH4OHABAD ROLD").
                # Recognizing Hindi as Hindi fixes this at the source rather
                # than trying to clean up its fallout afterward.
                _reader = easyocr.Reader(["en", "hi"], gpu=gpu)
    return _reader


# Confidence threshold below which an OCR detection is treated as noise
# (a misread watermark, QR-code artifact, or background pattern) rather than
# real text. Calibrated against garbled reads seen live — a real, if
# imperfect, text detection still typically clears this; the kind of
# fabricated-looking gibberish that comes from a low-confidence guess does
# not. Same reasoning as memory/knowledge.py's _MAX_RELEVANT_DISTANCE: a
# fixed empirical cutoff beats trusting every result as equally reliable.
_MIN_CONFIDENCE = 0.4


def image_to_text(data: bytes) -> str:
    """OCR raw image bytes (png/jpg/...) and return the recognized text,
    with low-confidence detections dropped and the remainder reassembled in
    reading order (top-to-bottom, left-to-right)."""
    import numpy as np
    from PIL import Image

    reader = _get_reader()
    image = np.array(Image.open(io.BytesIO(data)).convert("RGB"))
    with _reader_lock:
        # detail=1 (not the previous detail=0) to get a confidence score per
        # detection; paragraph=False so confidence survives per-line rather
        # than being discarded by paragraph grouping — the reading-order
        # sort below replaces what paragraph=True used to provide.
        detections = reader.readtext(image, detail=1, paragraph=False)
    row_height = max(image.shape[0] / 80, 1)
    detections.sort(key=lambda det: (round(det[0][0][1] / row_height), det[0][0][0]))
    lines = [text for _, text, confidence in detections if confidence >= _MIN_CONFIDENCE]
    return "\n".join(lines)


def pdf_page_to_image_bytes(pdf_bytes: bytes, page_index: int) -> bytes:
    """Render one page of a PDF to PNG bytes, for OCR-ing scanned pages that
    have no real text layer (pypdf's extract_text() returns empty for them)."""
    import pymupdf

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[page_index]
        return page.get_pixmap().tobytes("png")


_availability_lock = threading.Lock()


def _compute_availability() -> bool:
    try:
        # Just `import torch` alone can take a few seconds (CUDA library
        # loading) — same reasoning as agent/tts.py's warm_up().
        import torch  # noqa: F401
        import easyocr  # noqa: F401
        import pymupdf  # noqa: F401

        return True
    except Exception:
        logger.exception("OCR unavailable (missing deps)")
        return False


def warm_up() -> None:
    """Pre-computes availability so the first real request doesn't pay for
    it. Call once, in a background thread, from server.py's startup hook."""
    available()


def available() -> bool:
    """Whether OCR is available right now. Cached after the first call."""
    global _availability_cache
    if _availability_cache is None:
        with _availability_lock:
            if _availability_cache is None:
                _availability_cache = _compute_availability()
    return _availability_cache

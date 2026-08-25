"""
Tests for memory/ocr.py's confidence filtering, reading order, and reader
language configuration. Run with: pytest tests/
"""
import io
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory import ocr


def _fake_png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (100, 100), color="white").save(buf, format="PNG")
    return buf.getvalue()


def test_image_to_text_drops_low_confidence_detections():
    """A low-confidence detection (a misread watermark, QR-code artifact,
    background pattern, ...) is noise, not real text, and must not appear
    in the returned text at all. Same reasoning as knowledge.py's
    relevance-distance threshold: a fixed empirical cutoff beats trusting
    every result as equally reliable."""
    fake_reader = Mock()
    fake_reader.readtext.return_value = [
        ([[0, 0], [10, 0], [10, 10], [0, 10]], "Real Name", 0.9),
        ([[0, 20], [10, 20], [10, 30], [0, 30]], "gArB13sh", 0.1),
    ]
    with patch("memory.ocr._get_reader", return_value=fake_reader):
        text = ocr.image_to_text(_fake_png_bytes())
    assert "Real Name" in text
    assert "gArB13sh" not in text


def test_image_to_text_orders_detections_top_to_bottom():
    """detail=1/paragraph=False (needed to get per-detection confidence)
    doesn't guarantee reading order the way the previous paragraph=True
    grouping did — the code must reconstruct it itself."""
    fake_reader = Mock()
    fake_reader.readtext.return_value = [
        ([[0, 50], [10, 50], [10, 60], [0, 60]], "second line", 0.9),
        ([[0, 0], [10, 0], [10, 10], [0, 10]], "first line", 0.9),
    ]
    with patch("memory.ocr._get_reader", return_value=fake_reader):
        text = ocr.image_to_text(_fake_png_bytes())
    assert text.index("first line") < text.index("second line")


def test_reader_uses_english_and_hindi():
    """Regression test for a real quality bug found live: many documents
    worth OCR-ing here (Aadhaar cards especially) are bilingual, with
    English field values sitting right next to Devanagari labels. An
    English-only reader doesn't skip that Hindi text — it tries to force
    those glyphs into English predictions, and the resulting garbage
    bleeds into adjacent real fields too (confirmed live: an Aadhaar
    number came back as "AASHN XMLATAATnR64", an address as
    "SH4OHABAD ROLD"). Recognizing Hindi as Hindi is the actual fix."""
    ocr._reader = None
    try:
        with patch("torch.cuda.is_available", return_value=False), patch("easyocr.Reader") as mock_reader_cls:
            ocr._get_reader()
        mock_reader_cls.assert_called_once_with(["en", "hi"], gpu=False)
    finally:
        ocr._reader = None

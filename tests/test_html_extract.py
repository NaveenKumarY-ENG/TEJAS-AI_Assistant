"""
Tests for integrations/html_extract.py — the readable-text extraction
helper shared by memory/knowledge.py's ingest_url (Knowledge Base) and
tools/read_webpage_tool.py (Live.AI). See tests/test_knowledge.py's own
test_extract_text_from_html_strips_boilerplate for confirmation that
memory/knowledge.py's re-exported alias still behaves identically after
the extraction.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from integrations.html_extract import extract_readable_text


def test_strips_script_style_and_boilerplate_tags():
    html = """
    <html><head><style>body{color:red}</style><script>alert(1)</script></head>
    <body>
      <nav>Home | About</nav>
      <header>Site Header</header>
      <main><h1>Article</h1><p>The secret ingredient is saffron.</p></main>
      <aside>Related links</aside>
      <footer>Copyright 2026</footer>
    </body></html>
    """
    text = extract_readable_text(html)
    assert "saffron" in text
    assert "Home" not in text
    assert "Site Header" not in text
    assert "Related links" not in text
    assert "Copyright" not in text
    assert "alert" not in text
    assert "color:red" not in text


def test_collapses_blank_lines():
    html = "<body>\n\n\n<p>Line one</p>\n\n\n<p>Line two</p>\n\n</body>"
    text = extract_readable_text(html)
    assert text == "Line one\nLine two"


def test_empty_html_returns_empty_string():
    assert extract_readable_text("<html><body></body></html>") == ""

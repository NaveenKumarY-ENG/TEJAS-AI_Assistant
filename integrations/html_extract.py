"""Shared "fetch a page, strip boilerplate, return readable text" helper.

Originally lived only inside memory/knowledge.py (used by its ingest_url()
for Knowledge Base URL ingestion). Extracted here, unchanged, so
tools/read_webpage_tool.py (Live.AI's page-reading tool) can reuse the exact
same, already-proven extraction logic instead of duplicating it — same
"reuse existing infrastructure" principle applied everywhere else in this
codebase. memory/knowledge.py imports this back under its old name so its
own behavior and tests are unaffected.
"""


def extract_readable_text(html: str) -> str:
    """Strip script/style/nav/boilerplate and return the visible text.
    Deliberately BeautifulSoup + a tag-removal heuristic rather than
    trafilatura or similar — matches this codebase's existing preference
    for small custom logic over heavier libraries (e.g.
    memory/knowledge.py's _chunk_text, frontend/src/utils/text.ts's
    hand-rolled sentence splitter) for something this straightforward."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text(separator="\n").splitlines())
    return "\n".join(line for line in lines if line)

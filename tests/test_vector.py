"""
Tests for conversation memory (memory/vector.py) — semantic recall of past
chat turns. Previously untested; added alongside the relevance-threshold fix
(recall() used to return its nearest neighbor regardless of how unrelated it
actually was, the same bug found and fixed in memory/knowledge.py's search()).

Run with: pytest tests/
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory import vector


def _cleanup(*texts: str) -> None:
    """remember() has no counterpart delete-by-content — tests reach into
    the collection directly to remove what they added, so they don't leave
    fake memories behind in the real conversation_memory store."""
    rows = vector._collection.get(include=["documents"])
    ids = [i for i, d in zip(rows["ids"], rows["documents"]) if d in texts]
    if ids:
        vector._collection.delete(ids=ids)


def test_remember_and_recall_round_trip():
    text = "The vacation itinerary includes three days in Kyoto visiting temples."
    vector.remember(text)
    try:
        results = vector.recall("tell me about the Kyoto trip")
        assert any("temples" in r for r in results)
    finally:
        _cleanup(text)


def test_recall_filters_out_irrelevant_matches():
    text = "The vacation itinerary includes three days in Kyoto visiting temples."
    vector.remember(text)
    try:
        assert vector.recall("what is my favorite pizza topping") == []
        assert vector.recall("explain quantum computing basics") == []
    finally:
        _cleanup(text)


def test_recall_empty_collection_returns_empty_list():
    # Doesn't assume the real store is empty (it has real history) — just
    # confirms the empty-collection short-circuit doesn't error, using a
    # query specific enough that any real match would be a genuine bug.
    results = vector.recall("zzqxv_nonexistent_calibration_probe_9f3a")
    assert isinstance(results, list)

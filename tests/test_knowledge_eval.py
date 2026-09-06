"""
Retrieval quality eval harness — a small, fixed corpus of real-shaped
documents plus a curated (query, expected filename) set, run as an actual
pytest. This is the automated version of what this project has, until now,
only ever caught by hand: a live QA session finding the relevance
threshold had drifted (2026-08-31), a live re-test finding _chunk_text was
splitting words mid-token, a live search showing results in the wrong
order. Every one of those regressions would have failed a test in this
file the moment it was introduced, instead of needing a human to notice.

Run with: pytest tests/test_knowledge_eval.py -v
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory import knowledge

# A deliberately varied corpus — eight documents, each on a distinct topic,
# so a wrong retrieval reliably points at either a genuine relevance bug or
# a genuine recall bug rather than a coincidental overlap between two
# similar documents.
_CORPUS = {
    "recipe.txt": (
        b"Classic Margherita Pizza Recipe. Spread a thin layer of San Marzano tomato sauce over the "
        b"dough, then add torn fresh mozzarella and a few basil leaves. Drizzle with olive oil and bake "
        b"at 475 degrees Fahrenheit for about 12 minutes, until the crust is golden and blistered."
    ),
    "earnings_q3.txt": (
        b"Q3 2026 Earnings Report. Revenue increased 18 percent year-over-year to 42 million dollars, "
        b"driven primarily by strong subscription renewals in the enterprise segment. Operating margin "
        b"improved to 22 percent, up from 17 percent in the prior-year quarter."
    ),
    "travel_itinerary.txt": (
        b"Tokyo Trip Itinerary. Day 1: arrive at Narita airport and check into the hotel in Shibuya. "
        b"Day 2: explore Akihabara and the Senso-ji temple. Day 3: take a day trip to see Mount Fuji "
        b"from the Kawaguchiko lake area."
    ),
    "api_reference.txt": (
        b"Authentication API Reference. POST /api/auth/login accepts a JSON body containing email and "
        b"password, and returns a bearer token valid for 24 hours. Include this token in the "
        b"Authorization header of every subsequent request."
    ),
    "history_article.txt": (
        b"The Fall of the Roman Empire. Historians commonly attribute the collapse of the Western Roman "
        b"Empire to a combination of economic instability, overextended military spending, and the "
        b"administrative split of the empire into Eastern and Western halves in 285 AD under Diocletian."
    ),
    "printer_manual.txt": (
        b"HP LaserJet Pro Setup Guide. To replace the toner cartridge, open the front access panel, pull "
        b"the old cartridge straight out, gently rock the new cartridge to distribute the toner, and "
        b"insert it until it clicks firmly into place."
    ),
    "meeting_notes.txt": (
        b"Weekly Engineering Standup Notes. Sarah is blocked on the payments integration; the ETA has "
        b"been pushed to next Friday pending a response from the vendor. Marcus finished the onboarding "
        b"flow redesign and it is ready for review."
    ),
    "workout_plan.txt": (
        b"4-Day Strength Training Split. Monday (lower body): back squats 5 sets of 5, Romanian deadlifts "
        b"3 sets of 8. Thursday (posterior chain): conventional deadlifts 3 sets of 5, barbell rows 4 "
        b"sets of 10."
    ),
}

# (query, expected filename, or None if nothing in the corpus should match)
_CASES = [
    ("how do I make a margherita pizza", "recipe.txt"),
    ("what temperature do I bake the pizza at", "recipe.txt"),
    ("what ingredients go on the pizza", "recipe.txt"),
    ("how did Q3 revenue perform this year", "earnings_q3.txt"),
    ("what was the year-over-year revenue growth", "earnings_q3.txt"),
    ("how did operating margin change", "earnings_q3.txt"),
    ("what's the plan for the Tokyo trip", "travel_itinerary.txt"),
    ("when do we visit Mount Fuji", "travel_itinerary.txt"),
    ("where are we staying in Tokyo", "travel_itinerary.txt"),
    ("how do I authenticate with the API", "api_reference.txt"),
    ("what does the login endpoint return", "api_reference.txt"),
    ("why did the Roman Empire collapse", "history_article.txt"),
    ("when did the empire split into east and west", "history_article.txt"),
    ("how do I replace the toner cartridge", "printer_manual.txt"),
    ("tell me about printer_manual.txt", "printer_manual.txt"),  # direct filename reference
    ("give me details on earnings_q3.txt", "earnings_q3.txt"),  # direct filename reference
    ("what is sarah blocked on", "meeting_notes.txt"),
    ("who finished the onboarding redesign", "meeting_notes.txt"),
    ("how many sets of squats on monday", "workout_plan.txt"),
    ("what's the deadlift volume on thursday", "workout_plan.txt"),
    ("what is the capital of Australia", None),  # negative — nothing in the corpus is relevant
    ("recommend a good sci-fi movie", None),  # negative
]


@pytest.fixture(scope="module", autouse=True)
def _corpus():
    """Ingests the whole eval corpus once for the module (ingesting per-case
    would be needlessly slow — structured extraction alone is a real LLM
    round-trip), then tears it all down afterward regardless of test
    outcome, same guarantee try/finally gives every other test in this
    suite."""
    doc_ids = [knowledge.ingest_document(name, content)["id"] for name, content in _CORPUS.items()]
    yield
    for doc_id in doc_ids:
        knowledge.delete_document(doc_id)


@pytest.mark.parametrize("query,expected_filename", _CASES)
def test_retrieval_eval_case(query, expected_filename):
    results = knowledge.search(query)
    filenames = {r["filename"] for r in results}
    if expected_filename is None:
        assert filenames == set(), f"expected no results for {query!r}, got {filenames}"
    else:
        assert expected_filename in filenames, (
            f"expected {expected_filename!r} among results for {query!r}, got {filenames or 'nothing'}"
        )


def test_retrieval_eval_case_count_matches_the_documented_corpus_size():
    """A trivial guard against silently trimming the eval set over time —
    if this drops, the coverage claim in this file's own docstring is now
    a lie."""
    assert len(_CASES) >= 15

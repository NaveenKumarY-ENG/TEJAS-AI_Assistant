"""
Tests for the per-turn context-building logic in agent/loop.py.

Run with: pytest tests/
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.loop import (
    Agent,
    _is_cart_request_query,
    _is_explicit_web_query,
    _is_knowledge_listing_query,
    _is_reminder_query,
    _is_volatile_query,
    _repeated_tool_call_notice,
    _tool_call_key,
)


def test_is_volatile_query_detects_time_and_weather_questions():
    assert _is_volatile_query("what's the weather like today")
    assert _is_volatile_query("what time is it right now")
    assert _is_volatile_query("how much free space do I have")


def test_is_volatile_query_ignores_unrelated_questions():
    assert not _is_volatile_query("what are the details on my Aadhaar card")
    assert not _is_volatile_query("explain how neural networks work")


def test_is_knowledge_listing_query_detects_real_reported_phrasings():
    """Every phrasing here was reproduced live and led to a stale listing
    answer resurfacing (see test_chat_streaming_never_memorizes_a_knowledge_
    listing_answer below) — this must keep matching all of them."""
    assert _is_knowledge_listing_query("What do we have in the knowledge base currently?")
    assert _is_knowledge_listing_query("Can you reach out the knowledge base and tell me what you have currently?")
    assert _is_knowledge_listing_query("what is available in the knowledge vase")  # observed typo/mishearing
    assert _is_knowledge_listing_query("what is available in knowledge base")
    # Confirmed live as a real, actually-triggered bug: this exact typo'd
    # phrasing didn't match the old knowledge-literal pattern, so the answer
    # (naming a document that no longer existed) got memorized anyway and
    # kept being recalled as fact for every later listing question.
    assert _is_knowledge_listing_query("what does knowleddge base has")


def test_is_knowledge_listing_query_ignores_unrelated_questions():
    assert not _is_knowledge_listing_query("what does AIML.pdf cover")
    assert not _is_knowledge_listing_query("explain how neural networks work")


def test_is_reminder_query_detects_real_reported_phrasings():
    """Every phrasing here was reproduced live and led to a stale reminder
    answer (in one case, a completely fabricated "Buy milk" reminder that
    never existed) resurfacing in later turns via semantic recall — this
    must keep matching all of them."""
    assert _is_reminder_query("List my reminders.")
    assert _is_reminder_query("Remind me every Monday at 9am to submit my weekly timesheet.")
    assert _is_reminder_query("Reschedule my timesheet reminder to 2pm instead.")
    assert _is_reminder_query("Delete my timesheet reminder.")


def test_is_reminder_query_ignores_unrelated_questions():
    assert not _is_reminder_query("what's the weather like today")
    assert not _is_reminder_query("explain how neural networks work")


def test_is_cart_request_query_detects_real_reported_phrasing():
    """QA audit finding B-005: this exact phrasing (reproduced 3x live)
    silently never resulted in a cart action at all — the model called
    shop_amazon alone and narrated accessory recommendations instead. Must
    keep matching this and close variants."""
    assert _is_cart_request_query(
        "Add Samsung Galaxy S25+ 5G AI Smartphone (Silver Shadow, 12GB RAM, "
        "256GB Storage), 50MP Camera to cart."
    )
    assert _is_cart_request_query("add the iQOO Z11 to my cart")
    assert _is_cart_request_query("please put this in my cart")
    assert _is_cart_request_query("Add it to the cart")
    # Regression: found live right after the initial B-005 fix shipped —
    # "to my Amazon cart" (an extra word between "my" and "cart") is
    # completely natural phrasing that the original pattern missed
    # entirely, silently reproducing the exact same routing failure this
    # regex exists to prevent.
    assert _is_cart_request_query("Add a cheap plastic pen (under 50 rupees) to my Amazon cart.")
    assert _is_cart_request_query("add this to my amazon.in cart")


def test_is_cart_request_query_ignores_ambiguous_purchase_phrasing():
    """Deliberately does NOT match "order"/"buy" alone — those are too
    ambiguous (ordering food, buying a stock) and risk the opposite
    failure: forcing a real Amazon cart action nobody asked for. Only the
    unambiguous "add/put ... to/in cart" phrasing is treated as a strong
    enough signal to override the model's own tool choice."""
    assert not _is_cart_request_query("order me a pizza for dinner")
    assert not _is_cart_request_query("should I buy this stock")
    assert not _is_cart_request_query("what's in my cart right now")
    assert not _is_cart_request_query("how much is in my shopping cart")


def test_is_explicit_web_query_detects_real_reported_phrasing():
    """Confirmed live: "Search the web for best AIML courses" pulled in
    the user's own unrelated "AIML.pdf" as ambient knowledge-base context
    purely on a keyword coincidence, visibly confusing a 7B local model
    into trying to read the local document as if it were a web page."""
    assert _is_explicit_web_query("Search the web for best AIML courses")
    assert _is_explicit_web_query("Search the web for best AIML courses available on internet")
    assert _is_explicit_web_query("look up the latest iPhone price online")
    assert _is_explicit_web_query("find this on google")
    assert _is_explicit_web_query("can you research this on the internet")
    assert _is_explicit_web_query("what's the answer available on internet")


def test_is_explicit_web_query_ignores_unrelated_questions():
    """Deliberately narrow — a plain question with no web/internet/google
    wording must still go through the normal ambient knowledge-base path,
    not skip it just because it happens to contain "search" or "find" on
    their own (e.g. "find my report.pdf" should still search the KB)."""
    assert not _is_explicit_web_query("what does AIML.pdf cover")
    assert not _is_explicit_web_query("find my report.pdf")
    assert not _is_explicit_web_query("what's a good pizza topping")
    assert not _is_explicit_web_query("explain how neural networks work")


def _make_agent() -> Agent:
    return Agent()  # no session_id/resume -> fresh in-memory session, no real history to load


def test_messages_for_llm_searches_knowledge_base_unconditionally():
    """Regression test for the core fix: knowledge base search must run
    every turn, not be gated on any keyword/regex heuristic — an earlier
    version only searched when the message looked like it named a specific
    file, which a small local model still routinely talked its way around
    (repeatedly confirmed live). There must be nothing for the model to
    "decide" here at all."""
    fake_results = [{"filename": "notes.txt", "text": "The launch code is zebra-quartz-77."}]
    with patch("agent.loop.knowledge.search", return_value=fake_results) as mock_search:
        agent = _make_agent()
        messages, kb_results = agent._messages_for_llm("what's a good pizza topping")
    mock_search.assert_called_once_with("what's a good pizza topping", n_results=3)
    assert kb_results == fake_results
    assert "zebra-quartz-77" in messages[-1]["content"]


def test_messages_for_llm_omits_knowledge_section_when_nothing_relevant():
    with patch("agent.loop.knowledge.search", return_value=[]):
        agent = _make_agent()
        messages, kb_results = agent._messages_for_llm("what's a good pizza topping")
    assert kb_results == []
    assert "Relevant content from the knowledge base" not in messages[-1]["content"]


def test_messages_for_llm_skips_knowledge_base_for_explicit_web_queries():
    """The actual regression test for the live-reported bug: even when the
    knowledge base genuinely WOULD return something relevant (here, mocked
    exactly like the real "AIML.pdf" coincidentally matching "best AIML
    courses" did), an explicit "search the web" request must skip it
    entirely — knowledge.search() must not even be called — rather than
    injecting a contradictory second source for a 7B model to get confused
    between."""
    fake_results = [{"filename": "AIML.pdf", "text": "AIML stands for Artificial Intelligence and Machine Learning."}]
    with patch("agent.loop.knowledge.search", return_value=fake_results) as mock_search, patch(
        "agent.loop.knowledge.document_listing", return_value="- AIML.pdf"
    ):
        agent = _make_agent()
        messages, kb_results = agent._messages_for_llm("Search the web for best AIML courses")
    mock_search.assert_not_called()
    assert kb_results == []
    assert "Relevant content from the knowledge base" not in messages[-1]["content"]
    assert "Documents currently in the knowledge base" not in messages[-1]["content"]
    assert "explicitly asks for a live web search" in messages[-1]["content"]


def test_messages_for_llm_uses_format_search_results_for_the_injected_text():
    """The injected context must go through the same formatter the
    search_knowledge tool itself uses, so a document's Markdown table (an ID
    card's extracted fields) survives intact rather than being reformatted
    differently by two separate code paths."""
    fake_results = [{"filename": "id.jpg", "text": "**id.jpg**\n\n| Field | Value |\n|---|---|\n| Name | Jane |"}]
    with patch("agent.loop.knowledge.search", return_value=fake_results):
        agent = _make_agent()
        messages, _ = agent._messages_for_llm("tell me about id.jpg")
    assert "| Name | Jane |" in messages[-1]["content"]


def test_messages_for_llm_includes_document_listing_unconditionally():
    """Regression test: 'what is available in the knowledge base' is a
    listing question, not a content query — search() alone can't answer it
    (no chunk's text says "here is the complete list"). The listing must be
    ambient, present every turn, same as the search results and recalled
    memory above it — not dependent on the model deciding to ask for it."""
    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.knowledge.document_listing", return_value="- report.pdf (Invoice)\n- notes.txt"
    ):
        agent = _make_agent()
        messages, _ = agent._messages_for_llm("what's 2+2")
    assert "report.pdf (Invoice)" in messages[-1]["content"]
    assert "Documents currently in the knowledge base" in messages[-1]["content"]


def test_messages_for_llm_omits_listing_section_when_knowledge_base_empty():
    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.knowledge.document_listing", return_value=""
    ):
        agent = _make_agent()
        messages, _ = agent._messages_for_llm("what's 2+2")
    assert "Documents currently in the knowledge base" not in messages[-1]["content"]


def test_messages_for_llm_includes_reminder_listing_unconditionally():
    """Regression test for a real bug found live: asked to 'list my
    reminders,' a 7B local model sometimes skipped calling manage_reminders
    entirely and fabricated a plausible-looking answer instead — one live
    test invented a "Buy milk" reminder that never existed. Same fix as
    knowledge.document_listing() above: the real list must be ambient,
    present every turn, not dependent on the model deciding to call the tool."""
    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.structured.reminder_listing", return_value="- #3: Submit weekly timesheet (due 2026-08-31T09:00:00) [repeats weekly]"
    ):
        agent = _make_agent()
        messages, _ = agent._messages_for_llm("what's 2+2")
    assert "Submit weekly timesheet" in messages[-1]["content"]
    assert "Your current reminders" in messages[-1]["content"]


def test_messages_for_llm_omits_reminder_section_when_no_reminders():
    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.structured.reminder_listing", return_value=""
    ):
        agent = _make_agent()
        messages, _ = agent._messages_for_llm("what's 2+2")
    assert "Your current reminders" not in messages[-1]["content"]


def test_messages_for_llm_reinforces_order_amazon_for_a_cart_request():
    """QA audit finding B-005: a static system-prompt rule alone did NOT
    fix this — the model kept calling shop_amazon alone and narrating
    accessory recommendations instead of ever calling order_amazon. Same
    fix shape as the reminder-listing bug above: reinforce the instruction
    right next to the user's own message, every matching turn, rather than
    relying on a rule buried in the (cached, rarely re-attended-to) static
    system prompt."""
    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.structured.reminder_listing", return_value=""
    ):
        agent = _make_agent()
        messages, _ = agent._messages_for_llm(
            "Add Samsung Galaxy S25+ 5G AI Smartphone (Silver Shadow, 12GB RAM, "
            "256GB Storage), 50MP Camera to cart."
        )
    assert "must call order_amazon" in messages[-1]["content"].lower()
    assert "do not call shop_amazon alone" in messages[-1]["content"].lower()


def test_messages_for_llm_omits_cart_directive_for_unrelated_messages():
    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.structured.reminder_listing", return_value=""
    ):
        agent = _make_agent()
        messages, _ = agent._messages_for_llm("what's the weather like today")
    assert "order_amazon" not in messages[-1]["content"]


def test_tool_call_key_is_order_independent_and_distinguishes_arguments():
    assert _tool_call_key("open_website", {"site": "wikipedia"}) == _tool_call_key("open_website", {"site": "wikipedia"})
    # Argument order in the dict must not matter — the model can emit keys
    # in any order across two otherwise-identical calls.
    assert _tool_call_key("get_weather", {"city": "Delhi", "units": "metric"}) == _tool_call_key(
        "get_weather", {"units": "metric", "city": "Delhi"}
    )
    assert _tool_call_key("open_website", {"site": "wikipedia"}) != _tool_call_key("open_website", {"site": "github"})
    assert _tool_call_key("open_website", {"site": "x"}) != _tool_call_key("shop_flipkart", {"site": "x"})


def test_repeated_tool_call_notice_tells_the_model_to_stop_and_answer():
    notice = _repeated_tool_call_notice("open_website")
    assert "open_website" in notice
    assert "stop" in notice.lower() or "already" in notice.lower()


def _fake_stream(text: str):
    """A minimal call_llm_streaming-shaped generator: one chunk, no tool calls."""
    yield {"message": {"content": text}}


def _fake_tool_call_stream(name: str, arguments: dict):
    """A minimal call_llm_streaming-shaped generator: one tool call, no text."""
    yield {"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}}


def test_chat_streaming_skips_a_repeated_identical_tool_call():
    """The actual regression test for the real, live-reproduced bug
    (confirmed via the real SQLite-persisted conversation history): asked
    to "open wikipedia," a 7B local model called open_website, got back a
    real, unambiguous success, and then called the exact same tool with
    the exact same argument 7 more times in a row instead of ever
    replying — burning the whole turn's tool-call budget. The loop must
    detect an exact repeat and stop actually re-executing the tool,
    regardless of whether the model itself ever stops asking for it.
    With open_website's single-shot limit at 1 (see
    _SINGLE_SHOT_TOOL_CALL_LIMIT), a repeat of ANY kind — identical or
    not — now ends the turn immediately with the first real result,
    rather than waiting for the model to eventually give up and reply on
    its own."""
    call_count = {"n": 0}

    def fake_call_llm_streaming(messages, tools):
        call_count["n"] += 1
        if call_count["n"] <= 3:
            return _fake_tool_call_stream("open_website", {"site": "wikipedia"})
        return _fake_stream("Done!")

    executed = []

    def fake_execute_tool(name, args):
        executed.append((name, args))
        return "Opened https://www.wikipedia.org for you in a browser window."

    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.knowledge.document_listing", return_value=""
    ), patch("agent.loop.vector.recall", return_value=[]), patch("agent.loop.vector.remember"), patch(
        "agent.loop.call_llm_streaming", side_effect=fake_call_llm_streaming
    ), patch("agent.loop.execute_tool", side_effect=fake_execute_tool):
        agent = _make_agent()
        chunks = []
        final = agent.chat_streaming("open wikipedia", on_chunk=chunks.append)

    # The tool was genuinely EXECUTED only once — the model "asking" for
    # it 3 times must not mean 3 real browser navigations.
    assert executed == [("open_website", {"site": "wikipedia"})]
    # The turn ends right away with that first real result — never the
    # model's own later, unnecessary "Done!" reply, and never the generic
    # tool-call-limit fallback.
    assert "wikipedia" in final
    assert "tool-call limit" not in final


def test_chat_streaming_ends_the_turn_on_an_exact_repeat_of_any_tool_not_just_open_website():
    """Confirmed live as the same class of bug on a DIFFERENT tool and a
    DIFFERENT provider: asked about the weather in Leh, Gemini called
    get_weather({'city': 'Leh'}), got a real result, then called the
    EXACT same get_weather({'city': 'Leh'}) again 6 more times in a row —
    each one correctly skipped from re-executing (get_weather isn't in
    _SINGLE_SHOT_TOOL_CALL_LIMIT, since legitimately asking about several
    different cities in one turn must still work), but the model never
    respected the "you already called this" notice and just kept asking,
    burning the whole turn's iteration budget. Any exact (name, args)
    repeat — for any tool, not just the ones with a configured single-shot
    limit — must now end the turn immediately with the real result
    already in hand."""
    call_count = {"n": 0}

    def fake_call_llm_streaming(messages, tools):
        call_count["n"] += 1
        return _fake_tool_call_stream("get_weather", {"city": "Leh"})

    executed = []

    def fake_execute_tool(name, args):
        executed.append((name, args))
        return "Weather for Leh, India: Now: 11.6degC, humidity 30%"

    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.knowledge.document_listing", return_value=""
    ), patch("agent.loop.vector.recall", return_value=[]), patch("agent.loop.vector.remember"), patch(
        "agent.loop.call_llm_streaming", side_effect=fake_call_llm_streaming
    ), patch("agent.loop.execute_tool", side_effect=fake_execute_tool):
        agent = _make_agent()
        final = agent.chat_streaming("what is the weather in leh ladhak", on_chunk=lambda _: None)

    # Executed exactly once, regardless of how many times the model kept
    # asking (the fake model would ask forever — call_count is not capped
    # here on purpose, to prove the loop itself is what stops it).
    assert executed == [("get_weather", {"city": "Leh"})]
    assert "Leh" in final
    assert "tool-call limit" not in final
    # The loop didn't need to exhaust its iteration budget to get there.
    assert call_count["n"] <= 2


def test_chat_ends_the_turn_on_an_exact_repeat_of_any_tool_not_just_open_website():
    """Same fix, same regression, for the non-streaming chat()/
    _run_tool_loop twin."""
    call_count = {"n": 0}

    def fake_call_llm(messages, tools):
        call_count["n"] += 1
        return {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "get_weather", "arguments": {"city": "Leh"}}}],
            }
        }

    executed = []

    def fake_execute_tool(name, args):
        executed.append((name, args))
        return "Weather for Leh, India: Now: 11.6degC, humidity 30%"

    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.knowledge.document_listing", return_value=""
    ), patch("agent.loop.vector.recall", return_value=[]), patch("agent.loop.vector.remember"), patch(
        "agent.loop.call_llm", side_effect=fake_call_llm
    ), patch("agent.loop.execute_tool", side_effect=fake_execute_tool):
        agent = _make_agent()
        final = agent.chat("what is the weather in leh ladhak")

    assert executed == [("get_weather", {"city": "Leh"})]
    assert "Leh" in final
    assert "tool-call limit" not in final
    assert call_count["n"] <= 2


def test_chat_streaming_ends_the_turn_when_a_single_shot_tool_is_dodge_repeated():
    """The actual regression test for the real, SECOND live report: even
    with the exact-repeat dedup above in place, a local model could still
    dodge it by inventing a DIFFERENT argument on every retry instead of
    ever replying ("open g-shock" -> tool succeeds -> model calls
    open_website again with "example.com", out of nowhere) — each call
    individually looks "new" to a same-(name,args) check, so that guard
    alone never caught it. open_website must be capped by NAME alone: once
    it's already run _SINGLE_SHOT_TOOL_CALL_LIMIT times this turn, the turn
    ends immediately with the last real result — no further calls, no
    matter what argument the model tries next."""
    call_count = {"n": 0}
    sites = ["wikipedia", "example.com", "another-dodge.com", "yet-another.com"]

    def fake_call_llm_streaming(messages, tools):
        call_count["n"] += 1
        # A different, never-repeated argument every single time — the
        # exact shape that dodges the exact-(name, args) dedup.
        site = sites[min(call_count["n"] - 1, len(sites) - 1)]
        return _fake_tool_call_stream("open_website", {"site": site})

    executed = []

    def fake_execute_tool(name, args):
        executed.append((name, args))
        return f"Opened https://{args['site']} for you in a browser window."

    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.knowledge.document_listing", return_value=""
    ), patch("agent.loop.vector.recall", return_value=[]), patch("agent.loop.vector.remember"), patch(
        "agent.loop.call_llm_streaming", side_effect=fake_call_llm_streaming
    ), patch("agent.loop.execute_tool", side_effect=fake_execute_tool):
        agent = _make_agent()
        chunks = []
        final = agent.chat_streaming("open wikipedia", on_chunk=chunks.append)

    # Real executions capped at the configured limit (1) — explicit product
    # requirement: "open only once and valid once." Even though the model
    # kept trying with new arguments well past that, only the first (real,
    # correct) call is ever executed.
    assert executed == [("open_website", {"site": "wikipedia"})]
    # The turn ends with the FIRST (and only) real result as the answer —
    # never a later dodge call's wrong site, and never the generic "I hit
    # my tool-call limit" fallback.
    assert "wikipedia" in final
    assert "example.com" not in final
    assert "tool-call limit" not in final


def test_chat_streaming_only_fires_on_tool_for_calls_actually_executed():
    """Confirmed live as the real bug behind the ORIGINAL screenshot report
    (a stack of duplicate "open website" pills in the UI): on_tool used to
    fire once per tool call the MODEL asked for, before the single-shot
    cap/dedup logic ever decided whether to actually run it — so even
    after the backend was fixed to genuinely execute open_website only
    once, the UI still rendered a pill for every dodge/repeat attempt the
    model made. on_tool must fire exactly once per call that actually
    reaches execute_tool, never for one that gets capped or deduped away
    first."""
    call_count = {"n": 0}
    sites = ["wikipedia", "example.com", "another-dodge.com"]

    def fake_call_llm_streaming(messages, tools):
        call_count["n"] += 1
        site = sites[min(call_count["n"] - 1, len(sites) - 1)]
        return _fake_tool_call_stream("open_website", {"site": site})

    def fake_execute_tool(name, args):
        return f"Opened https://{args['site']} for you in a browser window."

    tool_pill_calls = []

    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.knowledge.document_listing", return_value=""
    ), patch("agent.loop.vector.recall", return_value=[]), patch("agent.loop.vector.remember"), patch(
        "agent.loop.call_llm_streaming", side_effect=fake_call_llm_streaming
    ), patch("agent.loop.execute_tool", side_effect=fake_execute_tool):
        agent = _make_agent()
        agent.chat_streaming("open wikipedia", on_chunk=lambda _: None, on_tool=tool_pill_calls.append)

    # Exactly one pill, even though the model asked for open_website 3
    # times in this turn — matches the one real execution, not the three
    # requests.
    assert tool_pill_calls == ["open_website"]


def test_chat_streaming_overrides_a_hallucinated_url_with_a_real_google_search():
    """The actual regression test for the real, SECOND live report on this
    same bug: even with resolve_url able to turn a "<topic> in google.com"
    phrase into a real Google search URL, and the system prompt telling
    the model to pass the phrase through, a local model still sometimes
    ignores both and fabricates its own specific, nonexistent article URL
    for the site argument (confirmed live:
    "https://www.example.com/live-cricket-score-afghan-vs-india" for
    "open Afgan vs india cricket live in google.com") — which
    "successfully" navigates to a 404 instead of a real page. The user's
    own original wording said "google.com" for this turn, so that must
    win over whatever fabricated argument the model chose."""

    def fake_call_llm_streaming(messages, tools):
        return _fake_tool_call_stream(
            "open_website", {"site": "https://www.example.com/live-cricket-score-afghan-vs-india"}
        )

    executed = []

    def fake_execute_tool(name, args):
        executed.append((name, args))
        return f"Opened {args['site']} for you in a browser window."

    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.knowledge.document_listing", return_value=""
    ), patch("agent.loop.vector.recall", return_value=[]), patch("agent.loop.vector.remember"), patch(
        "agent.loop.call_llm_streaming", side_effect=fake_call_llm_streaming
    ), patch("agent.loop.execute_tool", side_effect=fake_execute_tool):
        agent = _make_agent()
        final = agent.chat_streaming(
            "open Afgan vs india cricket live in google.com", on_chunk=lambda _: None
        )

    # The model's own fabricated URL never actually reaches execute_tool —
    # it's replaced with a real Google search built from the user's own
    # words before the tool ever runs.
    assert len(executed) == 1
    assert executed[0][0] == "open_website"
    assert executed[0][1]["site"] == "https://www.google.com/search?q=afgan+vs+india+cricket+live"
    assert "example.com" not in final


def test_chat_skips_a_repeated_identical_tool_call():
    """Same fix, same regression, for the non-streaming chat()/
    _run_tool_loop twin. With open_website's single-shot limit at 1, the
    turn ends immediately on the repeat with the first real result rather
    than waiting for the model to eventually reply "Done!" itself — see
    chat_streaming's identical test for the full story."""
    call_count = {"n": 0}

    def fake_call_llm(messages, tools):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "open_website", "arguments": {"site": "wikipedia"}}}],
                }
            }
        return {"message": {"content": "Done!", "tool_calls": []}}

    executed = []

    def fake_execute_tool(name, args):
        executed.append((name, args))
        return "Opened https://www.wikipedia.org for you in a browser window."

    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.knowledge.document_listing", return_value=""
    ), patch("agent.loop.vector.recall", return_value=[]), patch("agent.loop.vector.remember"), patch(
        "agent.loop.call_llm", side_effect=fake_call_llm
    ), patch("agent.loop.execute_tool", side_effect=fake_execute_tool):
        agent = _make_agent()
        final = agent.chat("open wikipedia")

    assert executed == [("open_website", {"site": "wikipedia"})]
    assert "wikipedia" in final
    assert "tool-call limit" not in final


def test_chat_ends_the_turn_when_a_single_shot_tool_is_dodge_repeated():
    """Same fix, same regression, for the non-streaming chat()/
    _run_tool_loop twin — see chat_streaming's identical test for the
    full story."""
    call_count = {"n": 0}
    sites = ["wikipedia", "example.com", "another-dodge.com", "yet-another.com"]

    def fake_call_llm(messages, tools):
        call_count["n"] += 1
        site = sites[min(call_count["n"] - 1, len(sites) - 1)]
        return {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "open_website", "arguments": {"site": site}}}],
            }
        }

    executed = []

    def fake_execute_tool(name, args):
        executed.append((name, args))
        return f"Opened https://{args['site']} for you in a browser window."

    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.knowledge.document_listing", return_value=""
    ), patch("agent.loop.vector.recall", return_value=[]), patch("agent.loop.vector.remember"), patch(
        "agent.loop.call_llm", side_effect=fake_call_llm
    ), patch("agent.loop.execute_tool", side_effect=fake_execute_tool):
        agent = _make_agent()
        final = agent.chat("open wikipedia")

    assert executed == [("open_website", {"site": "wikipedia"})]
    assert "wikipedia" in final
    assert "example.com" not in final
    assert "tool-call limit" not in final


def test_chat_streaming_appends_canonical_table_verbatim():
    """Regression test for the actual reported bug: even with an explicit
    "present this table as-is" system-prompt instruction, the model itself
    would still alter values when asked to retype a structured table (e.g.
    inventing a plausible-looking date for an illegible OCR field that
    matched nothing in the real source — confirmed live). The real table
    must reach the user byte-for-byte correct regardless of what the model
    generates, so it's appended by the code, not generated by the model."""
    fake_table = "**id.jpg** (Aadhaar Card)\n\n| Field | Value |\n|---|---|\n| Name | Arohaam |"
    fake_results = [{"filename": "id.jpg", "text": fake_table}]
    chunks_seen = []

    with patch("agent.loop.knowledge.search", return_value=fake_results), patch(
        "agent.loop.knowledge.document_listing", return_value=""
    ), patch("agent.loop.vector.recall", return_value=[]), patch("agent.loop.vector.remember"), patch(
        "agent.loop.call_llm_streaming", return_value=_fake_stream("Here are the details:")
    ):
        agent = _make_agent()
        final = agent.chat_streaming("tell me about id.jpg", on_chunk=chunks_seen.append)

    full_output = "".join(chunks_seen)
    assert "| Name | Arohaam |" in full_output
    assert "| Name | Arohaam |" in final
    # The model's own (short) text is still there too — this is additive,
    # not a replacement of its reply.
    assert "Here are the details:" in final


def test_chat_streaming_does_not_append_when_no_structured_results():
    """No structured document matched this turn — nothing should be
    appended beyond whatever the model itself said."""
    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.knowledge.document_listing", return_value=""
    ), patch("agent.loop.vector.recall", return_value=[]), patch("agent.loop.vector.remember"), patch(
        "agent.loop.call_llm_streaming", return_value=_fake_stream("Sure, here's a joke.")
    ):
        agent = _make_agent()
        final = agent.chat_streaming("tell me a joke", on_chunk=lambda _: None)
    assert final == "Sure, here's a joke."


def test_chat_streaming_never_memorizes_a_knowledge_grounded_answer():
    """Regression test for a real bug found live: a knowledge-base answer
    got memorized, and kept being recalled and repeated verbatim even after
    the underlying document's extraction was later corrected — the fix
    (re-extracting cleaner data) had no effect because the stale answer was
    still sitting in semantic memory, recalled fresh on every future
    question. A knowledge-grounded turn must never reach vector.remember(),
    same principle as VOLATILE_TOOLS (weather/date/etc.) — the source can
    change (re-extraction, a retag, a deletion), and memory has no way to
    know that happened."""
    fake_results = [{"filename": "id.jpg", "text": "some content"}]
    with patch("agent.loop.knowledge.search", return_value=fake_results), patch(
        "agent.loop.knowledge.document_listing", return_value=""
    ), patch("agent.loop.vector.recall", return_value=[]), patch(
        "agent.loop.vector.remember"
    ) as mock_remember, patch(
        "agent.loop.call_llm_streaming", return_value=_fake_stream("Here's what it says.")
    ):
        agent = _make_agent()
        agent.chat_streaming("tell me about id.jpg", on_chunk=lambda _: None)
    mock_remember.assert_not_called()


def test_chat_streaming_never_memorizes_a_knowledge_listing_answer():
    """Regression test for a real bug found live: 'what's in the knowledge
    base' is answered from knowledge.document_listing(), not
    knowledge.search() — so kb_results comes back empty and the
    kb_results-based memorization guard alone doesn't catch it. Confirmed
    live: an old listing answer (naming documents that had since been
    deleted) got memorized this way, and kept resurfacing — sometimes
    correct, sometimes not — for every future 'what's in the knowledge
    base' question, even after the actual documents changed. This must
    never be memorized, same as a knowledge.search()-grounded answer."""
    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.knowledge.document_listing", return_value="- AIML.pdf (General Document)"
    ), patch("agent.loop.vector.recall", return_value=[]), patch(
        "agent.loop.vector.remember"
    ) as mock_remember, patch(
        "agent.loop.call_llm_streaming",
        return_value=_fake_stream("You have AIML.pdf in your knowledge base."),
    ):
        agent = _make_agent()
        agent.chat_streaming(
            "What do we have in the knowledge base currently?", on_chunk=lambda _: None
        )
    mock_remember.assert_not_called()


def test_chat_streaming_never_memorizes_a_reminder_answer():
    """Regression test for a real bug found live: a 'list my reminders'
    exchange got memorized, and a LATER 'list my reminders' turn recalled
    that old exchange as "relevant past context" — in one confirmed case,
    reinforcing a completely fabricated "Buy milk" reminder that never
    existed in the real database, which kept resurfacing turn after turn.
    Reminders are exactly as volatile as knowledge-base listings and
    VOLATILE_TOOLS: the underlying data can change (add/update/complete/
    delete) at any moment, so a memorized answer about them has no way to
    know it's gone stale."""
    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.knowledge.document_listing", return_value=""
    ), patch("agent.loop.structured.reminder_listing", return_value="- #1: Buy milk"), patch(
        "agent.loop.vector.recall", return_value=[]
    ), patch(
        "agent.loop.vector.remember"
    ) as mock_remember, patch(
        "agent.loop.call_llm_streaming",
        return_value=_fake_stream("You have one reminder: Buy milk."),
    ):
        agent = _make_agent()
        agent.chat_streaming("List my reminders.", on_chunk=lambda _: None)
    mock_remember.assert_not_called()


def test_chat_streaming_still_memorizes_ordinary_turns():
    """The exclusion is specific to knowledge-grounded turns — an ordinary
    chat exchange with nothing relevant in the knowledge base should still
    be remembered, same as before this fix."""
    with patch("agent.loop.knowledge.search", return_value=[]), patch(
        "agent.loop.knowledge.document_listing", return_value=""
    ), patch("agent.loop.vector.recall", return_value=[]), patch(
        "agent.loop.vector.remember"
    ) as mock_remember, patch(
        "agent.loop.call_llm_streaming", return_value=_fake_stream("Sure, here's a joke.")
    ):
        agent = _make_agent()
        agent.chat_streaming("tell me a joke", on_chunk=lambda _: None)
    mock_remember.assert_called_once()

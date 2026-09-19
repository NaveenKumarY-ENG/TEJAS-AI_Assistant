"""
Central configuration for the assistant.
All tunables live here — nothing hardcoded elsewhere.
"""
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
# Overridable so the test suite can point this at an isolated temp directory
# instead of the real database — see tests/conftest.py, which sets this env
# var before anything else gets imported. Without it, tests like "a nonsense
# query returns no results" are only as reliable as whatever real data
# happens to be in the live knowledge base at the time (confirmed flaky
# twice: once against garbled OCR text, again against a real uploaded PDF).
DATA_DIR = Path(os.getenv("TEJAS_DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(exist_ok=True, parents=True)

# Models selectable at runtime from the UI (see /api/models in server.py).
# "id" is what the frontend sends back to select one; local Ollama models
# must already be pulled (`ollama pull <model>`) or the switch will fail at
# call time with a clear ollama error. The Anthropic entry only works if
# ANTHROPIC_API_KEY is set — enforced in Config.set_active_model below.
#
# "supports_tools": gemma2's Ollama template does not implement tool-calling
# at all — passing `tools=` to it makes Ollama reject the request outright
# (400 "does not support tools"), which would crash every single turn.
# agent/llm_client.py checks this flag and simply omits the tools payload
# for such models, so they still work as a plain (tool-less) chat model
# instead of erroring on every message. Confirmed by live testing.
AVAILABLE_MODELS: list[dict] = [
    {"id": "ollama:qwen2.5:7b", "provider": "ollama", "model": "qwen2.5:7b", "label": "Qwen 2.5 7B (Local)", "supports_tools": True},
    {"id": "ollama:qwen3:8b", "provider": "ollama", "model": "qwen3:8b", "label": "Qwen 3 8B (Local)", "supports_tools": True},
    {"id": "ollama:llama3.1:latest", "provider": "ollama", "model": "llama3.1:latest", "label": "Llama 3.1 8B (Local)", "supports_tools": True},
    {"id": "ollama:gemma2:9b", "provider": "ollama", "model": "gemma2:9b", "label": "Gemma 2 9B (Local, no tools)", "supports_tools": False},
    {"id": "anthropic:claude-sonnet-4-6", "provider": "anthropic", "model": "claude-sonnet-4-6", "label": "Claude Sonnet (Cloud)", "supports_tools": True},
    {"id": "gemini:gemini-3.6-flash", "provider": "gemini", "model": "gemini-3.6-flash", "label": "Gemini 3.6 Flash (Cloud)", "supports_tools": True},
]

# Neural voice options for TTS_PROVIDER=neural (see agent/tts.py). Kokoro's
# voice is a per-call parameter, not baked into the pipeline object, so
# switching between these takes effect on the very next /api/tts call — no
# reload needed. Confirmed against Kokoro's real voice pack list and its
# published per-voice quality grades: am_michael/am_fenrir are both solid
# mid-tier male voices (grade C+); af_heart is the highest-quality voice
# overall (grade A) and was the original default before TEJAS was made male.
AVAILABLE_TTS_VOICES: list[dict] = [
    {"id": "am_michael", "label": "Michael (Male)"},
    {"id": "am_fenrir", "label": "Fenrir (Male)"},
    {"id": "af_heart", "label": "Heart (Female)"},
]


@dataclass
class Config:
    # --- LLM ---
    # "ollama" (local, free, private) or "anthropic" (hosted, costs money,
    # much stronger tool-calling).
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "ollama").strip().lower())
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("ASSISTANT_MODEL", "claude-sonnet-4-6"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5:7b"))
    # Free tier available with no billing/card required (aistudio.google.com/apikey) —
    # unlike Anthropic, a good zero-cost bridge to a stronger-than-local model.
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    # gemini-3.6-flash (not the newer 3.7) — confirmed live that 3.7-flash's
    # free tier is capped at just 20 requests/day, too tight for real use;
    # an older, more established model has more provisioned free-tier capacity.
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.6-flash"))
    # Small local models drift away from facts given directly in the system
    # prompt (e.g. inventing a different date than the one just stated) at
    # Ollama's default sampling temperature (~0.8) — confirmed by testing:
    # qwen2.5:7b hallucinated a wrong date at temperature 0.2 but answered
    # correctly, verbatim from the injected fact, at temperature 0. Fully
    # deterministic decoding is worth the tradeoff of less "creative"
    # phrasing for an assistant whose job is to be factually reliable.
    llm_temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.0")))
    # How long Ollama keeps a model loaded in memory after the last request.
    # Ollama's own default is 5 minutes; on CPU-only hardware, reloading a
    # ~5GB model from disk costs 10+ seconds on top of inference time, so
    # this keeps it warm for a realistic single-session gap instead of
    # paying that reload cost on every message.
    ollama_keep_alive: str = field(default_factory=lambda: os.getenv("OLLAMA_KEEP_ALIVE", "30m"))
    # Confirmed live as a real, reported bug: with no num_ctx set, Ollama
    # silently uses its own runtime default of 4096 tokens regardless of a
    # model's real trained context length (qwen2.5:7b supports 32768) —
    # `ollama ps` showed the loaded instance capped at exactly 4096. This
    # app's own tool schemas alone run several hundred tokens, on top of
    # the system prompt and up to max_history_messages of prior
    # conversation, so a longer-running session can genuinely overflow
    # 4096 — and when it does, Ollama truncates from the context rather
    # than erroring, which can silently drop the tool definitions
    # entirely. That produces exactly this: the model insists no tools
    # are available for something it actually has a tool for (get_weather
    # for "weather in Leh"), because as far as its truncated context is
    # concerned, none were ever given to it. 8192 comfortably covers this
    # app's real prompt sizes without the extra memory/compute cost of
    # going all the way to the model's full 32768.
    ollama_num_ctx: int = field(default_factory=lambda: int(os.getenv("OLLAMA_NUM_CTX", "8192")))
    max_tokens: int = 1024
    max_tool_iterations: int = 8  # hard cap to prevent infinite tool-call loops
    max_history_messages: int = 20  # keep last N messages; older ones are dropped
    # --- Assistant identity ---
    assistant_name: str = field(default_factory=lambda: os.getenv("ASSISTANT_NAME", "TEJAS"))
    system_prompt: str = (
        "You are {name}, a sharp, efficient personal AI assistant.\n\n"
        "CRITICAL RULES ABOUT TOOLS:\n"
        "- You have real, working tools. You MUST actually call them - never pretend to.\n"
        "- Never write phrases like 'let me search' or 'Results:' unless a tool was "
        "genuinely invoked and returned something. If you cannot call a tool, say so plainly.\n"
        "- For anything about current events, weather, prices, news, or facts you are not "
        "certain of, you MUST call web_search instead of answering from memory.\n"
        "- For ANY request to translate a word/phrase into another language, or asking what "
        "something is called in another language, you MUST call web_search to verify — never "
        "answer directly from memory, no matter how confident it feels. Confirmed live as a "
        "real failure mode: asked what 'apple' is called in Kannada, a local model answered "
        "with a completely made-up word instead of the real one (ಸೇಬು), stated just as "
        "confidently as a correct answer would have been. Small local models cannot reliably "
        "judge their own uncertainty here — they produce fluent, confident-sounding "
        "translations that are simply wrong, especially into scripts/languages that are less "
        "common in training data — so treat every translation request as something you're "
        "never certain of by default, not something to gauge case-by-case.\n"
        "- For arithmetic, data processing, or anything computational, call execute_python "
        "rather than working it out in your head.\n"
        "- Never invent or fabricate tool results. Only report what a tool actually returned.\n"
        "- After a tool call succeeds, your reply MUST state the actual result — the number, "
        "fact, or value itself — never just describe that you called a tool or that it "
        "'returned a result' without saying what that result was. Confirmed live as a real "
        "failure mode: asked to calculate something, a model called execute_python "
        "successfully but replied only 'I called the execute_python tool to calculate the "
        "result' — never actually telling the user what the answer was. The user needs the "
        "answer, not a narration of your own tool use.\n"
        "- Once a tool call succeeds and its result actually answers the request, STOP calling "
        "tools and reply with that result — do not call the exact same tool with the exact same "
        "input again hoping for a different outcome. Confirmed live as a real, separate failure "
        "mode: asked to 'open wikipedia,' a model called open_website, got back 'Opened "
        "https://www.wikipedia.org for you in a browser window' (a real, unambiguous success), and "
        "then called open_website with the identical input 7 more times in a row instead of just "
        "telling the user it was done — burning the entire tool-call budget for the turn on "
        "repeats of a call that had already succeeded the first time. A successful tool result "
        "never needs to be re-confirmed by calling the same tool again with the same arguments.\n"
        "- If a tool returns an error or says it is not configured, tell the user that "
        "plainly and STOP THERE. Do not follow it with 'however, based on what I know...' "
        "or any other guessed answer — a tool failure disclosed honestly is far better than "
        "a confident-sounding guess that might be wrong. This applies even if you feel sure "
        "you remember the answer: you do not have a way to verify it without the tool, so "
        "say the lookup failed and ask if the user wants you to try again, instead of stating "
        "unverified facts as if they were reliable. When the tool's own message includes a "
        "specific reason (a URL it tried, an error detail, what it actually attempted) — as "
        "open_website/shop_amazon/shop_flipkart/read_webpage's failure messages always do — "
        "include that specific detail in your reply, not just a generic 'there was an issue.' "
        "A vague paraphrase throws away the one thing that would let the user tell a real, "
        "worth-reporting problem apart from something transient worth just retrying.\n"
        "- Your context may include a 'Documents currently in the knowledge base' listing and/or "
        "a 'Relevant content from the knowledge base' section — both already fetched for you "
        "from the user's own uploaded documents. Use the listing to answer questions about what "
        "exists (e.g. 'what's in my knowledge base') directly, without calling any tool. Do NOT "
        "call search_knowledge again if the 'Relevant content' section already answers the "
        "question — only call it for a genuinely different or more specific query than what's "
        "already there. If you're told a document's exact field table will be shown "
        "automatically after your reply, do not retype, reformat, or guess at any of its values "
        "yourself — just write one short introductory sentence and stop. Never call "
        "search_knowledge for a question that has nothing to do with the user's own uploaded "
        "documents (arithmetic, weather, dates, system info, general knowledge, etc.) — use the "
        "tool that actually matches the question, or answer directly.\n"
        "- Your context may include a 'Your current reminders' listing — real data, already "
        "fetched. Use it to answer 'list my reminders' directly, and to find the correct "
        "reminder_id when asked to update/complete/delete one by description. This listing is "
        "read-only reference material, though: adding, updating, completing, or deleting a "
        "reminder STILL requires an actual manage_reminders call every single time — never say "
        "a reminder was added/changed/removed without that call actually succeeding this turn, "
        "even if the listing makes it look obvious what should happen, and even if you recall "
        "doing so earlier in this conversation.\n"
        "- order_amazon adds an item to cart and reaches Amazon's checkout REVIEW page only — it "
        "has no ability to complete a purchase, no matter what it's asked to do. Never claim to "
        "have placed an order or completed a purchase. If asked to 'complete the purchase' or "
        "'place the order', explain plainly that this assistant stops at checkout review by "
        "design and the user needs to click 'Place your order' themselves in the browser window.\n"
        "- When the user asks to add/order/buy a SPECIFIC product ('add X to cart', 'order this', "
        "'buy the Y'), you MUST call order_amazon with that product (by name or link) as the very "
        "next step. Do NOT call shop_amazon alone and then describe the search results, recommend "
        "accessories, or suggest alternatives instead — that is not what was asked, even if the "
        "search results include accessories or similar items alongside the actual product. If "
        "order_amazon reports it couldn't confirm an exact match (wrong variant, not found, etc.), "
        "relay that message plainly rather than substituting your own recommendation for it.\n"
        "- For Amazon, use shop_amazon (search/browse) and order_amazon (add to cart + reach "
        "checkout review). For Flipkart, use shop_flipkart to search/browse — it is research-only "
        "and CANNOT add to cart or purchase anything, no matter how it's asked. For any other "
        "website the user names or pastes a link to, use open_website to open it, and read_webpage "
        "if you need to actually read/summarize a page's content. Never call open_website for "
        "Amazon or Flipkart when the user wants to search or shop there — use the dedicated tool "
        "instead, it does a real search rather than just opening a homepage. When calling "
        "open_website for a specific known site, pass a short site name or the real URL — NOT a "
        "descriptive phrase like 'diesel watches official website.' If a web_search (or any earlier "
        "tool result) already surfaced the real URL for what the user's asking about, pass that "
        "exact URL to open_website rather than re-describing the site from scratch — confirmed live "
        "as a real failure mode: describing it instead of reusing the already-known real URL "
        "produced a wrong, guessed-at domain that failed to load. NEVER invent a specific article/ "
        "page URL you don't actually know is real (e.g. guessing at a path like "
        "'.../live-score-india-afghan') just to have something to pass — confirmed live as a worse, "
        "separate failure mode: the browser 'successfully' opens the fake URL and lands on a 404, "
        "so it LOOKS like it worked but tells the user nothing real. For 'open <topic> in google.com' "
        "/ 'search <topic> on google' (a topic, not one specific site), pass the request through "
        "close to as-is as open_website's site argument (e.g. site='afghan vs india cricket live in "
        "google.com') — it is automatically turned into a real Google search URL; do not try to "
        "guess a more specific URL yourself. If you're unsure whether a URL is real, call web_search "
        "first instead of guessing.\n"
        "- If asked to buy, order, add to cart, or checkout on Flipkart or any other non-Amazon "
        "site, say plainly that only Amazon ordering is supported right now, and offer to open the "
        "site or search it for them instead. Never say something was added to a cart or an order "
        "was placed unless a real tool call actually confirmed it — same rule as order_amazon, "
        "applied to every site, not just Amazon.\n"
        "- Results from web_search, open_website, read_webpage, shop_amazon, and shop_flipkart are "
        "live, real-time lookups, not from memory — say so plainly (e.g. 'just checked' / 'right "
        "now'), never present them as something you already knew. If you gather the same kind of "
        "information from two different live sources in one turn (e.g. a price from both Amazon "
        "and Flipkart) and they disagree, state the disagreement explicitly rather than silently "
        "picking one.\n\n"
        "Be concise. Confirm before doing anything destructive or irreversible."
    )

    # --- Memory ---
    sqlite_path: str = str(DATA_DIR / "assistant.db")
    vector_store_path: str = str(DATA_DIR / "vector_store")

    # --- Web search (optional, e.g. Tavily/Serper) ---
    search_api_key: str = field(default_factory=lambda: os.getenv("SEARCH_API_KEY", ""))

    # --- Live.AI (real-time web browsing/reading + Flipkart research) ---
    # A real, working kill switch: when false, open_website/read_webpage/
    # shop_flipkart all self-report disabled from inside run() (same
    # soft-fail pattern shop_amazon already uses for browser.available()),
    # /api/meta reports it too so the frontend hides the Live.AI nav entry
    # entirely — everything else in the app (chat, voice, knowledge base,
    # shop_amazon/order_amazon) is completely unaffected either way.
    live_ai_enabled: bool = field(default_factory=lambda: os.getenv("LIVE_AI_ENABLED", "1") == "1")

    # --- Speech-to-text (voice input) ---
    # "local" (faster-whisper, offline, free) or "openai" (Whisper API, hosted).
    # The openai path automatically falls back to local on failure, so voice
    # input keeps working even if the cloud call errors or the key is missing.
    stt_provider: str = field(default_factory=lambda: os.getenv("STT_PROVIDER", "local").strip().lower())
    whisper_model: str = field(default_factory=lambda: os.getenv("WHISPER_MODEL", "base.en"))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_stt_model: str = field(default_factory=lambda: os.getenv("OPENAI_STT_MODEL", "whisper-1"))

    # --- Text-to-speech (voice output) ---
    # "neural" (Kokoro-82M, GPU-accelerated when CUDA is available, falls
    # back to CPU otherwise) or "browser" (disable server-side TTS entirely;
    # the frontend uses the browser's built-in SpeechSynthesis instead, as
    # it always did before this existed). "neural" also degrades to the
    # browser voice automatically at runtime if torch/kokoro aren't
    # installed or fail to load — see agent/tts.py's available().
    tts_provider: str = field(default_factory=lambda: os.getenv("TTS_PROVIDER", "neural").strip().lower())
    tts_voice: str = field(default_factory=lambda: os.getenv("TTS_VOICE", "am_michael"))
    # Kokoro's language/voice-pack prefix — "a" = American English. Must
    # match the prefix of tts_voice (e.g. "af_..."/"am_..." both use "a").
    tts_lang_code: str = field(default_factory=lambda: os.getenv("TTS_LANG_CODE", "a"))

    # "auto" (default) uses CUDA for TTS/OCR whenever it's available, same
    # as always. "cpu" forces CPU regardless — added after live-diagnosing
    # a real, measurable problem on a shared 6GB GPU: Ollama's own LLM
    # (~4.7GB of weights alone for a 7B Q4 model) plus Kokoro/EasyOCR's
    # CUDA allocation together didn't fit in 6GB, so Ollama silently
    # offloaded part of the model to CPU (confirmed via `ollama ps`: an
    # 18-30% CPU/GPU split depending on model/context) — every single chat
    # response paying for that split, not just TTS/OCR calls. TTS/OCR are
    # comparatively small models (Kokoro-82M; EasyOCR's detector/
    # recognizer) that run acceptably on CPU, and are only invoked
    # occasionally (a reply's speech, an uploaded document) rather than on
    # every conversation turn — freeing the GPU for Ollama alone is the
    # better trade on a card too small to hold both comfortably. Leave as
    # "auto" if your GPU has enough headroom for everything at once (8GB+
    # generally does, for the model sizes this app ships with).
    tts_device: str = field(default_factory=lambda: os.getenv("TTS_DEVICE", "auto").strip().lower())
    ocr_device: str = field(default_factory=lambda: os.getenv("OCR_DEVICE", "auto").strip().lower())

    # --- Safety ---
    sandbox_dir: str = str(DATA_DIR / "sandbox")  # code exec / file ops confined here

    def static_system_prompt(self) -> str:
        """
        The system prompt with NO live/per-call content mixed in (no clock),
        so it's byte-identical across every call for a given model/config —
        confirmed by direct testing that Ollama caches the KV state for a
        matching prompt prefix across separate requests, and reusing that
        cache (instead of reprocessing the ~800-token tool schema from
        scratch every time) cuts real response time roughly 3x. Only
        changes when the active model's tool support changes (i.e. when the
        user switches models), which is rare enough not to hurt caching.

        The live clock is injected per-turn instead — see
        current_time_context() and agent/loop.py's _messages_for_llm,
        which attaches it to the outgoing user message the same way
        recalled memory context already is, never to this static prompt.
        """
        base = self.system_prompt.format(name=self.assistant_name)
        if not self.active_model_supports_tools:
            base += (
                "\n\nNote: this model has no tool access right now (no web "
                "search, code execution, reminders, or file access). Answer "
                "from your own knowledge, and say plainly when something "
                "would need a live lookup or tool call you can't perform "
                "instead of pretending to have done one."
            )
        return base

    def current_time_context(self) -> str:
        """
        The live date/time grounding, kept separate from the system prompt
        (see static_system_prompt) so embedding it doesn't defeat Ollama's
        prompt-prefix cache on every single call. Small local models are
        unreliable about actually invoking a tool for "what's today's date"
        instead of just narrating one from training data — grounding the
        real time directly removes that failure mode, since it no longer
        depends on the model choosing to call anything.
        """
        now = datetime.now().strftime("%A, %d %B %Y, %I:%M %p")
        return (
            f"Current date and time: {now} (system local time). "
            "Treat this as ground truth for any question about today's date, "
            "the current time, or the day of the week — never guess or state "
            "a different date. Only use the get_current_datetime tool if the "
            "user asks for the time in a specific different timezone."
        )

    @property
    def active_model(self) -> str:
        """The model name actually in use, given the selected provider."""
        if self.llm_provider == "anthropic":
            return self.model
        if self.llm_provider == "gemini":
            return self.gemini_model
        return self.ollama_model

    @property
    def active_model_id(self) -> str:
        """Matches an AVAILABLE_MODELS "id" when the active model is one of
        the presets; otherwise a synthesized id for a custom .env value."""
        return f"{self.llm_provider}:{self.active_model}"

    @property
    def active_model_supports_tools(self) -> bool:
        """True unless the active model is a known preset flagged
        supports_tools=False (see AVAILABLE_MODELS). Defaults True for a
        custom/unlisted model, matching this app's behavior before model
        switching existed."""
        entry = next((m for m in AVAILABLE_MODELS if m["id"] == self.active_model_id), None)
        return entry["supports_tools"] if entry else True

    def set_active_model(self, model_id: str) -> None:
        """Switch models at runtime — read fresh on every LLM call (see
        agent/llm_client.py), so this takes effect on the very next turn with
        no restart or reconnect needed."""
        entry = next((m for m in AVAILABLE_MODELS if m["id"] == model_id), None)
        if entry is None:
            raise ValueError(f"Unknown model id: {model_id}")
        if entry["provider"] == "anthropic" and not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set — cannot switch to an Anthropic model.")
        if entry["provider"] == "gemini" and not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is not set — cannot switch to a Gemini model.")

        self.llm_provider = entry["provider"]
        if entry["provider"] == "ollama":
            self.ollama_model = entry["model"]
        elif entry["provider"] == "gemini":
            self.gemini_model = entry["model"]
        else:
            self.model = entry["model"]

    def set_active_tts_voice(self, voice_id: str) -> None:
        """Switch the neural TTS voice at runtime — agent/tts.py's
        synthesize() reads config.tts_voice fresh on every call (Kokoro's
        voice is a per-call parameter, not baked into the pipeline), so this
        takes effect on the very next /api/tts request with no reload."""
        entry = next((v for v in AVAILABLE_TTS_VOICES if v["id"] == voice_id), None)
        if entry is None:
            raise ValueError(f"Unknown TTS voice id: {voice_id}")
        self.tts_voice = entry["id"]


config = Config()
Path(config.sandbox_dir).mkdir(exist_ok=True, parents=True)
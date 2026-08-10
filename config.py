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
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


@dataclass
class Config:
    # --- LLM ---
    # "ollama" (local, free, private) or "anthropic" (hosted, costs money,
    # much stronger tool-calling).
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "ollama").strip().lower())
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("ASSISTANT_MODEL", "claude-sonnet-4-6"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5:7b"))
    # Small local models drift away from facts given directly in the system
    # prompt (e.g. inventing a different date than the one just stated) at
    # Ollama's default sampling temperature (~0.8) — confirmed by testing:
    # qwen2.5:7b hallucinated a wrong date at temperature 0.2 but answered
    # correctly, verbatim from the injected fact, at temperature 0. Fully
    # deterministic decoding is worth the tradeoff of less "creative"
    # phrasing for an assistant whose job is to be factually reliable.
    llm_temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.0")))
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
        "- For arithmetic, data processing, or anything computational, call execute_python "
        "rather than working it out in your head.\n"
        "- Never invent or fabricate tool results. Only report what a tool actually returned.\n"
        "- If a tool returns an error or says it is not configured, tell the user that "
        "plainly rather than making up an answer.\n\n"
        "Be concise. Confirm before doing anything destructive or irreversible."
    )

    # --- Memory ---
    sqlite_path: str = str(DATA_DIR / "assistant.db")
    vector_store_path: str = str(DATA_DIR / "vector_store")

    # --- Web search (optional, e.g. Tavily/Serper) ---
    search_api_key: str = field(default_factory=lambda: os.getenv("SEARCH_API_KEY", ""))

    # --- Speech-to-text (voice input) ---
    # "local" (faster-whisper, offline, free) or "openai" (Whisper API, hosted).
    # The openai path automatically falls back to local on failure, so voice
    # input keeps working even if the cloud call errors or the key is missing.
    stt_provider: str = field(default_factory=lambda: os.getenv("STT_PROVIDER", "local").strip().lower())
    whisper_model: str = field(default_factory=lambda: os.getenv("WHISPER_MODEL", "base.en"))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_stt_model: str = field(default_factory=lambda: os.getenv("OPENAI_STT_MODEL", "whisper-1"))

    # --- Safety ---
    sandbox_dir: str = str(DATA_DIR / "sandbox")  # code exec / file ops confined here

    def formatted_system_prompt(self) -> str:
        """
        Rebuilt fresh on every LLM call (see agent/llm_client.py) so the
        injected clock is never stale. Small local models are unreliable
        about actually invoking a tool for "what's today's date" instead of
        just narrating one from training data — grounding the real time
        directly in the prompt removes that failure mode entirely, since it
        no longer depends on the model choosing to call anything.
        """
        base = self.system_prompt.format(name=self.assistant_name)
        now = datetime.now().strftime("%A, %d %B %Y, %I:%M %p")
        return (
            f"{base}\n\nCurrent date and time: {now} (system local time). "
            "Treat this as ground truth for any question about today's date, "
            "the current time, or the day of the week — never guess or state "
            "a different date. Only use the get_current_datetime tool if the "
            "user asks for the time in a specific different timezone."
        )

    @property
    def active_model(self) -> str:
        """The model name actually in use, given the selected provider."""
        return self.model if self.llm_provider == "anthropic" else self.ollama_model


config = Config()
Path(config.sandbox_dir).mkdir(exist_ok=True, parents=True)
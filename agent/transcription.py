"""Speech-to-text for voice input.

Supports two providers behind one function, same pattern as llm_client's
dual Ollama/Anthropic support:

- "local": faster-whisper running on-device. Free, offline, no API key.
  The model loads lazily on first use (a few seconds) and stays cached.
- "openai": OpenAI's hosted Whisper API. Faster/more accurate on modest
  hardware, but costs money and needs network + an API key. If it fails
  for any reason (missing key, network error, rate limit), we fall back
  to the local model rather than losing voice input entirely.
"""
import io
import logging

import requests

from config import config

logger = logging.getLogger("assistant.transcription")

_local_model = None


def _get_local_model():
    global _local_model
    if _local_model is None:
        from faster_whisper import WhisperModel

        logger.info("Loading local Whisper model '%s' (first use only)...", config.whisper_model)
        _local_model = WhisperModel(config.whisper_model, device="cpu", compute_type="int8")
    return _local_model


def _transcribe_local(audio_bytes: bytes) -> str:
    model = _get_local_model()
    segments, _info = model.transcribe(io.BytesIO(audio_bytes), language="en", vad_filter=True)
    return " ".join(segment.text.strip() for segment in segments).strip()


def _transcribe_openai(audio_bytes: bytes) -> str:
    if not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured for cloud transcription.")
    response = requests.post(
        "https://api.openai.com/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {config.openai_api_key}"},
        files={"file": ("speech.webm", audio_bytes, "audio/webm")},
        data={"model": config.openai_stt_model},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("text", "").strip()


def transcribe(audio_bytes: bytes) -> str:
    """Transcribe raw audio bytes (any ffmpeg-readable format) to text."""
    if config.stt_provider == "openai":
        try:
            return _transcribe_openai(audio_bytes)
        except Exception:
            logger.exception("Cloud transcription failed, falling back to local Whisper")
    return _transcribe_local(audio_bytes)

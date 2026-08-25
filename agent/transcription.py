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

# Whisper has no dedicated "silence" token — trained on huge amounts of
# scraped video with silent/near-silent stretches (intros, outros, pauses),
# it resolves those to short filler phrases instead ("you", "Thank you.",
# "Bye.", ...) rather than an empty transcription. Confirmed live and
# reproduced directly against this exact model+pipeline: 2 seconds of quiet
# noise (no real speech at all) transcribed as "Thank you." with
# no_speech_prob=0.91 and a poor avg_logprob=-0.93 — i.e. the model's own
# metadata already flags it as very likely not real speech, but nothing
# was checking that. A segment failing either check is discarded rather
# than trusted, on the theory that a false "didn't catch that" (the user
# just tries again) is a far better failure mode than confidently handing
# the chat model a hallucinated sentence and letting it improvise a reply
# to something the user never said.
_MAX_NO_SPEECH_PROB = 0.6
_MIN_AVG_LOGPROB = -1.0


def _get_local_model():
    global _local_model
    if _local_model is None:
        from faster_whisper import WhisperModel

        logger.info("Loading local Whisper model '%s' (first use only)...", config.whisper_model)
        _local_model = WhisperModel(config.whisper_model, device="cpu", compute_type="int8")
    return _local_model


def _join_confident_segments(segments) -> tuple[str, int, int]:
    """Filters out low-confidence segments (see _MAX_NO_SPEECH_PROB's
    comment) before joining. Returns (text, kept_count, dropped_count) —
    the counts are for logging, so a hallucination-rejection is visible in
    the logs rather than silently indistinguishable from genuine silence."""
    kept = []
    dropped = 0
    for segment in segments:
        if segment.no_speech_prob > _MAX_NO_SPEECH_PROB or segment.avg_logprob < _MIN_AVG_LOGPROB:
            dropped += 1
            continue
        kept.append(segment.text.strip())
    return " ".join(kept).strip(), len(kept), dropped


def _transcribe_local(audio_bytes: bytes) -> str:
    model = _get_local_model()
    segments, info = model.transcribe(io.BytesIO(audio_bytes), language="en", vad_filter=True)
    text, kept, dropped = _join_confident_segments(segments)

    if not text:
        # The VAD filter can misclassify real mic input (quiet levels,
        # background noise, a mic's specific frequency response) as
        # non-speech and strip the whole clip to zero segments - a false
        # "no speech" is worse than the filter's noise-reduction benefit,
        # so retry once without it before giving up. Still goes through
        # the same confidence filter above — a no-VAD retry on genuinely
        # silent/near-silent audio is exactly the scenario that produces
        # a hallucinated "you"/"Thank you.", so this pass needs the
        # confidence check even more than the first one did.
        logger.warning(
            "VAD-filtered transcription came back empty (%.2fs of audio, %d low-confidence segment(s) dropped) "
            "- retrying without VAD filter",
            info.duration,
            dropped,
        )
        segments, info = model.transcribe(io.BytesIO(audio_bytes), language="en", vad_filter=False)
        text, kept, dropped = _join_confident_segments(segments)
        if dropped and not kept:
            logger.info(
                "No-VAD retry produced only low-confidence segment(s) (likely silence hallucination) - "
                "treating %.2fs of audio as no speech detected",
                info.duration,
            )

    logger.info("Transcribed %.2fs of audio -> %d char(s) (%d segment(s) kept, %d dropped)",
                info.duration, len(text), kept, dropped)
    return text


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

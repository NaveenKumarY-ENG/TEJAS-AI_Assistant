"""Text-to-speech: server-side neural voice via Kokoro-82M, GPU-accelerated
when CUDA is available (falls back to CPU automatically otherwise).

Same lazy-singleton, fail-soft shape as agent/transcription.py: the model
loads on first use (a few seconds, plus a one-time ~300MB weights download),
and any missing dependency or runtime error is caught rather than crashing
the request — the frontend falls back to the browser's built-in TTS when
this reports itself unavailable (see /api/meta's "tts_available" field).
"""
import io
import logging
import threading

from config import config

logger = logging.getLogger("assistant.tts")

_pipeline = None
_pipeline_lock = threading.Lock()
_availability_cache: bool | None = None


def _to_numpy(audio):
    """Kokoro yields torch tensors (possibly on GPU); soundfile needs numpy."""
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    return audio


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        # Double-checked locking: without this, two concurrent first callers
        # (e.g. two browser tabs' first /api/tts request landing close
        # together) would both pass the outer None-check and each construct
        # their own KPipeline — doubling GPU memory transiently, which can
        # OOM on a small card. This same lock is reused in synthesize()
        # below to also serialize actual inference calls, not just this
        # one-time construction — see that comment for why.
        with _pipeline_lock:
            if _pipeline is None:
                import torch
                from kokoro import KPipeline

                device = "cuda" if torch.cuda.is_available() else "cpu"
                logger.info(
                    "Loading Kokoro TTS pipeline on %s (first use only, may download weights)...",
                    device,
                )
                _pipeline = KPipeline(lang_code=config.tts_lang_code, device=device)
    return _pipeline


def synthesize(text: str) -> bytes:
    """Synthesize speech for `text`, returning WAV bytes (24kHz mono)."""
    if config.tts_provider != "neural":
        raise RuntimeError("Neural TTS is disabled (TTS_PROVIDER is not 'neural')")

    import numpy as np
    import soundfile as sf

    pipeline = _get_pipeline()
    # Kokoro's pipeline isn't documented as safe for concurrent forward
    # passes — two requests racing on the same shared GPU state (two tabs,
    # or Voice Mode + a normal chat tab both speaking around the same time)
    # risks corrupted/interleaved audio or CUDA errors. A single GPU
    # serializes real work anyway, so holding this lock for the actual
    # inference call (not just _get_pipeline's one-time construction above)
    # costs nothing in practice.
    with _pipeline_lock:
        chunks = [_to_numpy(audio) for _, _, audio in pipeline(text, voice=config.tts_voice, speed=1)]
    if not chunks:
        raise RuntimeError("Kokoro produced no audio output")
    audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]

    buf = io.BytesIO()
    sf.write(buf, audio, 24000, format="WAV")
    return buf.getvalue()


_availability_lock = threading.Lock()


def _compute_availability() -> bool:
    if config.tts_provider != "neural":
        return False
    try:
        # Just `import torch` alone routinely takes several seconds (CUDA
        # library loading), even without constructing the full pipeline —
        # so this is "cheaper than a full model load," not actually cheap.
        # That's exactly why warm_up() below runs this off the request path.
        import torch  # noqa: F401
        import kokoro  # noqa: F401

        return True
    except Exception:
        logger.exception("Neural TTS unavailable (missing deps)")
        return False


def warm_up() -> None:
    """
    Pre-computes availability so the first real request doesn't pay for it.
    Call once, in a background thread, from server.py's startup hook — by
    the time a browser tab actually loads the page and fires its first
    /api/meta fetch, this has usually already finished, so that request
    sees an instant cache hit instead of blocking on `import torch`.
    """
    available()


def available() -> bool:
    """
    Whether neural TTS is available right now. Cached after the first call.
    If warm_up() hasn't finished yet (or was never run — e.g. a bare `python
    -c` script, not the real server), this computes it synchronously as a
    fallback, so it's still correct on its own — just not free.
    """
    global _availability_cache
    if _availability_cache is None:
        with _availability_lock:
            if _availability_cache is None:
                _availability_cache = _compute_availability()
    return _availability_cache

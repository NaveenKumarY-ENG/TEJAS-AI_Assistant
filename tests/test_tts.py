"""
Tests for agent/tts.py's device selection. No real audio/model involved —
`kokoro`/`torch` are mocked at the boundary, same approach as
tests/test_ocr.py. Run with: pytest tests/
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import tts


def test_get_pipeline_respects_tts_device_cpu_override_even_with_cuda_available():
    """Regression test for a real, live-diagnosed problem: on a GPU too
    small to hold both Ollama's LLM and Kokoro/EasyOCR's own CUDA
    allocation at once, Kokoro defaulting to GPU whenever CUDA is merely
    *available* (regardless of whether it's actually a good idea) forced
    Ollama to partially offload its model to CPU (confirmed via `ollama
    ps`: an 18-30% CPU/GPU split) — every chat response paying for it, not
    just TTS calls. TTS_DEVICE=cpu must override CUDA availability, not
    just supplement it."""
    tts._pipeline = None
    fake_kokoro = MagicMock()
    fake_kokoro.KPipeline = MagicMock()
    try:
        with (
            patch("torch.cuda.is_available", return_value=True),  # CUDA IS available...
            patch("agent.tts.config.tts_device", "cpu"),  # ...but explicitly overridden to CPU
            patch.dict(sys.modules, {"torch": MagicMock(cuda=MagicMock(is_available=lambda: True)), "kokoro": fake_kokoro}),
        ):
            tts._get_pipeline()
        fake_kokoro.KPipeline.assert_called_once()
        assert fake_kokoro.KPipeline.call_args.kwargs["device"] == "cpu"
    finally:
        tts._pipeline = None


def test_get_pipeline_uses_gpu_when_device_is_auto_and_cuda_is_available():
    """The default ("auto") must not regress — this is still the right
    choice on a GPU with enough headroom for everything at once."""
    tts._pipeline = None
    fake_kokoro = MagicMock()
    fake_kokoro.KPipeline = MagicMock()
    try:
        with (
            patch("agent.tts.config.tts_device", "auto"),
            patch.dict(sys.modules, {"torch": MagicMock(cuda=MagicMock(is_available=lambda: True)), "kokoro": fake_kokoro}),
        ):
            tts._get_pipeline()
        assert fake_kokoro.KPipeline.call_args.kwargs["device"] == "cuda"
    finally:
        tts._pipeline = None


def test_get_pipeline_falls_back_to_cpu_when_auto_and_no_cuda():
    tts._pipeline = None
    fake_kokoro = MagicMock()
    fake_kokoro.KPipeline = MagicMock()
    try:
        with (
            patch("agent.tts.config.tts_device", "auto"),
            patch.dict(sys.modules, {"torch": MagicMock(cuda=MagicMock(is_available=lambda: False)), "kokoro": fake_kokoro}),
        ):
            tts._get_pipeline()
        assert fake_kokoro.KPipeline.call_args.kwargs["device"] == "cpu"
    finally:
        tts._pipeline = None

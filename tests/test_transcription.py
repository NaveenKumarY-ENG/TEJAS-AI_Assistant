"""
Tests for agent/transcription.py's local-Whisper confidence filtering.
Run with: pytest tests/
"""
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import transcription


@dataclass
class FakeSegment:
    text: str
    no_speech_prob: float = 0.05
    avg_logprob: float = -0.2


@dataclass
class FakeInfo:
    duration: float = 2.0


def _fake_model(vad_result, no_vad_result):
    """A stand-in for faster_whisper's WhisperModel — .transcribe() returns
    different segment lists depending on vad_filter, matching how the real
    call is made twice (first with vad_filter=True, then a no-VAD retry)."""
    model = MagicMock()

    def transcribe(_audio, language, vad_filter):
        segments = vad_result if vad_filter else no_vad_result
        return segments, FakeInfo()

    model.transcribe.side_effect = transcribe
    return model


def test_real_speech_passes_through_unfiltered():
    """A normal, confident transcription must not be touched by the new
    confidence filter — only hallucination-shaped low-confidence segments
    should ever be dropped."""
    segments = [FakeSegment("Hello, this is a real sentence.", no_speech_prob=0.02, avg_logprob=-0.15)]
    with patch("agent.transcription._get_local_model", return_value=_fake_model(segments, [])):
        text = transcription._transcribe_local(b"fake audio bytes")
    assert text == "Hello, this is a real sentence."


def test_silence_hallucination_is_rejected_not_trusted():
    """Regression test for a real bug found live: on quiet/silent audio,
    Whisper's VAD filter correctly returns zero segments, but the existing
    fallback then retried WITHOUT the VAD filter and blindly trusted
    whatever came back — and a no-VAD pass on genuinely silent audio is
    exactly the scenario where Whisper hallucinates short filler phrases
    ("you", "Thank you.", ...) instead of recognizing silence, because it
    has no dedicated silence token. Confirmed live and reproduced directly
    against the real model: 2s of quiet noise transcribed as "Thank you."
    with no_speech_prob=0.91 and avg_logprob=-0.93 — exactly the shape
    reproduced here. The chat model then had no way to know the user
    never actually said anything, and improvised a reply to "you"."""
    hallucinated = [FakeSegment("Thank you.", no_speech_prob=0.91, avg_logprob=-0.93)]
    with patch("agent.transcription._get_local_model", return_value=_fake_model([], hallucinated)):
        text = transcription._transcribe_local(b"fake audio bytes")
    assert text == ""


def test_low_confidence_by_avg_logprob_alone_is_also_rejected():
    """A segment can have a plausible-looking no_speech_prob but still be a
    poor/garbled guess — avg_logprob is the model's own confidence in the
    text it produced, and is checked independently, not only as a backstop
    to no_speech_prob."""
    segments = [FakeSegment("mumble garble", no_speech_prob=0.1, avg_logprob=-1.8)]
    with patch("agent.transcription._get_local_model", return_value=_fake_model(segments, [])):
        text = transcription._transcribe_local(b"fake audio bytes")
    assert text == ""


def test_mixed_confidence_segments_keep_only_the_confident_ones():
    """A multi-segment clip where only part of it is a hallucination should
    keep the real speech and drop just the bad segment, not discard
    everything or keep everything indiscriminately."""
    segments = [
        FakeSegment("This part is real.", no_speech_prob=0.05, avg_logprob=-0.2),
        FakeSegment("you", no_speech_prob=0.88, avg_logprob=-1.2),
    ]
    with patch("agent.transcription._get_local_model", return_value=_fake_model(segments, [])):
        text = transcription._transcribe_local(b"fake audio bytes")
    assert text == "This part is real."

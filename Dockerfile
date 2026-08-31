# TEJAS backend — serves the API, the WebSocket chat, and the already-built
# frontend (frontend/dist/, checked into the repo) statically from the same
# process, exactly like `uvicorn server:app` does when you run it directly.
#
# NOT included here, deliberately:
#   - CUDA/GPU support for torch (TTS/OCR). Runs on CPU by default — slower,
#     but fully functional, the same graceful degradation this app already
#     has built in (see agent/tts.py, memory/ocr.py). Wiring a GPU into this
#     image needs the NVIDIA Container Toolkit on the host and a CUDA base
#     image — out of scope for a default `docker compose up`, but the
#     Dockerfile itself needs no structural change to add it later.
#   - `playwright install chromium`. The Amazon shopping tools
#     (shop_amazon/order_amazon/view_amazon_cart) launch a REAL, VISIBLE
#     browser window (integrations/browser.py uses headless=False on
#     purpose — you need to actually see and use it to log in and review
#     checkout) — that fundamentally doesn't fit a headless container
#     without X11/VNC forwarding, which this image doesn't set up. Every
#     other tool works normally; shopping tools report themselves
#     unavailable the same way they already do when Playwright's browser
#     isn't installed at all (see README's Setup section) — run those
#     specific features outside Docker, or add your own X11 forwarding.
FROM python:3.12-slim

WORKDIR /app

# ffmpeg: faster-whisper's audio decoding (agent/transcription.py) and
# soundfile/kokoro's audio I/O (agent/tts.py) both go through it.
# build-essential: several ML-adjacent packages here (chromadb's vector
# index, easyocr's dependencies) can need a real compiler for a
# platform-specific wheel that isn't prebuilt for every base image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# No CUDA-specific torch install here (see the header comment above) —
# `pip install -r requirements.txt` alone pulls PyPI's default CPU-only
# torch wheel transitively via kokoro/easyocr, which is exactly the
# graceful non-GPU path this app already supports.
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# data/ (SQLite, ChromaDB, the Amazon browser profile, sandboxed files) is
# meant to persist across container restarts — mount it as a volume (see
# docker-compose.yml) rather than baking any of it into the image.
RUN mkdir -p data

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]

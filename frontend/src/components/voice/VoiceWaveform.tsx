import { useEffect, useRef } from "react";
import { readRmsLevel } from "../../utils/audio";
import { prefersReducedMotion } from "../../utils/motion";
import type { TtsBoundarySignal } from "../../hooks/useSpeechSynthesis";

export type WaveformMode = "microphone" | "tts" | "idle";

interface VoiceWaveformProps {
  mode: WaveformMode;
  analyserRef?: React.RefObject<AnalyserNode | null>;
  boundaryRef?: React.RefObject<TtsBoundarySignal | null>;
  className?: string;
}

// Mirrors AssistantCore.tsx's CORE_COLORS — intentionally duplicated here
// rather than sharing a module for two hex strings used in two places.
const COLOR_MAIN = "#ff7a1a";
const COLOR_SOFT = "#ffb454";

const BAR_COUNT = 24;
const WIDTH = 220;
const HEIGHT = 36;
// How fast a TTS word-boundary pulse decays — this is a cadence
// approximation, not real audio amplitude (browser TTS exposes no audio
// buffer to read from).
const PULSE_DECAY_MS = 260;

function drawBars(ctx: CanvasRenderingContext2D, heights: number[]) {
  ctx.clearRect(0, 0, WIDTH, HEIGHT);
  const gap = 3;
  const barWidth = (WIDTH - gap * (BAR_COUNT - 1)) / BAR_COUNT;
  for (let i = 0; i < BAR_COUNT; i++) {
    const h = Math.max(2, heights[i] * HEIGHT);
    const x = i * (barWidth + gap);
    const y = (HEIGHT - h) / 2;
    ctx.fillStyle = i % 2 === 0 ? COLOR_MAIN : COLOR_SOFT;
    ctx.globalAlpha = 0.55 + heights[i] * 0.45;
    ctx.fillRect(x, y, barWidth, h);
  }
  ctx.globalAlpha = 1;
}

/**
 * Small reusable waveform display. "microphone" mode visualizes real mic
 * input via a shared AnalyserNode (see utils/audio.ts). "tts" mode has no
 * real audio data available (browser SpeechSynthesis exposes none), so it
 * approximates speech cadence from word-boundary event timing instead.
 * "idle" mode is pure ambient decoration, no audio APIs involved.
 */
export function VoiceWaveform({ mode, analyserRef, boundaryRef, className }: VoiceWaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const bufferRef = useRef<Uint8Array<ArrayBuffer> | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    canvas.width = WIDTH;
    canvas.height = HEIGHT;

    if (prefersReducedMotion()) {
      drawBars(ctx, Array.from({ length: BAR_COUNT }, () => 0.15));
      return;
    }

    let frame: number;

    if (mode === "microphone") {
      const tick = () => {
        const analyser = analyserRef?.current;
        if (analyser) {
          if (!bufferRef.current || bufferRef.current.length !== analyser.fftSize) {
            bufferRef.current = new Uint8Array(analyser.fftSize);
          }
          const level = readRmsLevel(analyser, bufferRef.current);
          const heights = Array.from({ length: BAR_COUNT }, () =>
            Math.min(1, level * 4 * (0.6 + Math.random() * 0.8))
          );
          drawBars(ctx, heights);
        } else {
          drawBars(ctx, Array.from({ length: BAR_COUNT }, () => 0.08));
        }
        frame = requestAnimationFrame(tick);
      };
      frame = requestAnimationFrame(tick);
    } else if (mode === "tts") {
      const tick = () => {
        const signal = boundaryRef?.current;
        const elapsed = signal ? performance.now() - signal.ts : Infinity;
        const envelope = Math.max(0, 1 - elapsed / PULSE_DECAY_MS);
        const heights = Array.from({ length: BAR_COUNT }, (_, i) => {
          const centerBias = 1 - Math.abs(i - BAR_COUNT / 2) / (BAR_COUNT / 2);
          return Math.min(1, envelope * (0.4 + centerBias * 0.8) * (0.7 + Math.random() * 0.5));
        });
        drawBars(ctx, heights);
        frame = requestAnimationFrame(tick);
      };
      frame = requestAnimationFrame(tick);
    } else {
      const start = performance.now();
      const tick = (t: number) => {
        const elapsed = t - start;
        const base = Math.sin(elapsed * 0.002) * 0.15 + 0.2;
        const heights = Array.from({ length: BAR_COUNT }, (_, i) => Math.max(0.05, base + Math.sin(i * 0.6) * 0.05));
        drawBars(ctx, heights);
        frame = requestAnimationFrame(tick);
      };
      frame = requestAnimationFrame(tick);
    }

    return () => cancelAnimationFrame(frame);
  }, [mode, analyserRef, boundaryRef]);

  return <canvas ref={canvasRef} aria-hidden="true" className={className} style={{ width: WIDTH, height: HEIGHT }} />;
}

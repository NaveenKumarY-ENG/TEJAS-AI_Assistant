// Every tunable timing/threshold introduced by the voice pipeline rework,
// named so magic numbers don't get scattered across hooks/components.

/** ms of sustained quiet after detected speech before auto-stopping the mic. */
export const SILENCE_TIMEOUT_MS = 1200;

/**
 * Master switch for voice-activity-detection auto-stop. OFF by default:
 * plain click-to-start/click-to-stop is the version verified end-to-end
 * with real audio in this session, and is the safer default until VAD is
 * confirmed reliable across more devices/browsers. The mic-level analyser
 * still attaches either way (so the waveform still works), only the
 * auto-stop-on-silence trigger is gated by this flag.
 */
export const VAD_AUTO_STOP_ENABLED = false;

/**
 * Master switch for attaching a Web Audio API analyser to the mic stream at
 * all (powers both VAD and the microphone-mode waveform). If voice input
 * still doesn't work with VAD_AUTO_STOP_ENABLED off, flip this to false too
 * — it fully isolates the original, simplest recording path (record →
 * upload → transcribe, zero Web Audio API involvement) to rule out any
 * interaction between AudioContext and MediaRecorder as the cause.
 */
export const MIC_ANALYSER_ENABLED = true;

/** Normalized 0..1 RMS floor treated as "speech" during VAD. Approximate —
 * mic gain varies a lot across devices/OS, tune by ear if it triggers too
 * eagerly (lower) or not at all (raise). */
export const SILENCE_RMS_THRESHOLD = 0.02;

/** How long coreState stays "error" before automatically returning to idle. */
export const ERROR_DISPLAY_MS = 3500;

/** How long the HUD transcript reveal pill lingers after speech is transcribed. */
export const TRANSCRIPT_REVEAL_MS = 900;

// WebSocket reconnect backoff: base * factor^attempt, capped, plus jitter to
// avoid a thundering herd if the backend restarts with multiple tabs open.
export const RECONNECT_BASE_MS = 1000;
export const RECONNECT_FACTOR = 2;
export const RECONNECT_MAX_MS = 30_000;
export const RECONNECT_JITTER_MS = 250;

/** Delay before the spoken on-load greeting fires, tuned to land roughly
 * when IntroSequence's reveal animation finishes (dot expands ~2s in, then
 * the shell + visual greeting fade in) rather than talking before the UI
 * has visually appeared. */
export const GREETING_DELAY_MS = 2200;

// Shared audio-graph helpers so useSpeechRecognition (voice activity
// detection) and VoiceWaveform (microphone-mode visualization) can both read
// live mic levels from the SAME analyser, without opening a second
// getUserMedia stream (which would mean a second permission prompt / extra
// stream overhead for no benefit).

export interface MicAnalyserHandle {
  audioContext: AudioContext;
  analyser: AnalyserNode;
  dispose: () => void;
}

/**
 * Wraps an already-open MediaStream with an AnalyserNode for level metering.
 * Analysis-only — never connected to audioContext.destination, so there's no
 * feedback/echo risk from routing the mic back out to speakers.
 */
export function attachMicAnalyser(stream: MediaStream): MicAnalyserHandle {
  const audioContext = new AudioContext();
  // Some browsers create the context suspended even after a user gesture
  // (the gesture was "spent" on the getUserMedia prompt, not this call).
  if (audioContext.state === "suspended") void audioContext.resume();

  const source = audioContext.createMediaStreamSource(stream);
  const analyser = audioContext.createAnalyser();
  analyser.fftSize = 256;
  analyser.smoothingTimeConstant = 0.75;
  source.connect(analyser);

  let disposed = false;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    source.disconnect();
    analyser.disconnect();
    void audioContext.close();
  };

  return { audioContext, analyser, dispose };
}

/** RMS amplitude (0..1) from an analyser's current time-domain buffer. */
export function readRmsLevel(analyser: AnalyserNode, buffer: Uint8Array<ArrayBuffer>): number {
  analyser.getByteTimeDomainData(buffer);
  let sumSquares = 0;
  for (let i = 0; i < buffer.length; i++) {
    const centered = (buffer[i] - 128) / 128;
    sumSquares += centered * centered;
  }
  return Math.sqrt(sumSquares / buffer.length);
}

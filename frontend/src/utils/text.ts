/**
 * TTS engines (Kokoro and the browser's SpeechSynthesis) treat an all-caps
 * word as an acronym/initialism and spell it out letter by letter
 * ("T. E. J. A. S.") instead of pronouncing it as a name. This rewrites an
 * all-caps assistant name to Title Case wherever it appears in text bound
 * for speech, without touching anything else — display text elsewhere
 * keeps ASSISTANT_NAME's configured casing (e.g. "TEJAS") for branding.
 * A name that isn't fully uppercase (or is too short to matter) is left
 * alone, so this only ever fires for the acronym-spelling failure mode.
 */
export function humanizeForSpeech(text: string, assistantName: string): string {
  if (!assistantName || assistantName.length < 2 || assistantName !== assistantName.toUpperCase()) {
    return text;
  }
  const titleCased = assistantName[0] + assistantName.slice(1).toLowerCase();
  const escaped = assistantName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return text.replace(new RegExp(`\\b${escaped}\\b`, "g"), titleCased);
}

/**
 * Strips Markdown syntax before text is spoken — without this, a reply
 * containing "**Naruto**" gets read aloud as "asterisk asterisk Naruto
 * asterisk asterisk" by both TTS engines, since neither interprets the
 * characters as formatting when handed raw. Display text is unaffected —
 * see ConversationPanel.tsx, which renders Markdown properly instead of
 * stripping it. Applied per-sentence (after extractCompleteSentences), so a
 * bold/italic span that happens to get split across two streamed sentence
 * chunks may lose its pairing — the trailing catch-all below still removes
 * any leftover stray marker either way, so nothing gets read as literal
 * punctuation even in that edge case.
 */
export function stripMarkdownForSpeech(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, (block) => block.replace(/```\w*\n?/g, "").replace(/```/g, ""))
    .replace(/`([^`]+)`/g, "$1")
    .replace(/(\*\*\*|___)(.+?)\1/g, "$2")
    .replace(/(\*\*|__)(.+?)\1/g, "$2")
    .replace(/(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^>\s?/gm, "")
    .replace(/^[-*+]\s+/gm, "")
    .replace(/^\d+\.\s+/gm, "")
    .replace(/^(-{3,}|\*{3,}|_{3,})\s*$/gm, "")
    .replace(/[*_#`]/g, "")
    .trim();
}

/**
 * Splits a growing text buffer into "complete" sentences plus whatever
 * trailing fragment hasn't reached a sentence boundary yet. Used to feed
 * streamed assistant text into speech synthesis one sentence at a time
 * instead of waiting for the whole reply (too slow) or speaking raw
 * word-by-word fragments (unintelligible).
 */
export function extractCompleteSentences(buffer: string): { complete: string[]; remainder: string } {
  const complete: string[] = [];
  let rest = buffer;

  // A sentence ends at ., !, or ? followed by whitespace (or end of string
  // handled by the caller once streaming is done) — greedy enough to also
  // swallow trailing quotes/brackets before the whitespace.
  const boundary = /[.!?]["')\]]*\s+/;

  let match = rest.match(boundary);
  while (match && match.index !== undefined) {
    const end = match.index + match[0].length;
    const sentence = rest.slice(0, end).trim();
    if (sentence) complete.push(sentence);
    rest = rest.slice(end);
    match = rest.match(boundary);
  }

  return { complete, remainder: rest };
}

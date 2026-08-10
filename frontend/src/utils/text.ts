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

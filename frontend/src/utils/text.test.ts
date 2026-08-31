import { describe, expect, it } from "vitest";

import { extractCompleteSentences, humanizeForSpeech, stripMarkdownForSpeech } from "./text";

describe("humanizeForSpeech", () => {
  it("rewrites an all-caps assistant name to Title Case so TTS doesn't spell it out", () => {
    expect(humanizeForSpeech("Hello, I'm TEJAS, how can I help?", "TEJAS")).toBe(
      "Hello, I'm Tejas, how can I help?"
    );
  });

  it("rewrites every occurrence, not just the first", () => {
    expect(humanizeForSpeech("TEJAS here. TEJAS again.", "TEJAS")).toBe("Tejas here. Tejas again.");
  });

  it("leaves text alone when the name is already mixed-case", () => {
    expect(humanizeForSpeech("Hello, I'm Tejas.", "Tejas")).toBe("Hello, I'm Tejas.");
  });

  it("leaves text alone for a too-short name", () => {
    expect(humanizeForSpeech("Hi, I'm X.", "X")).toBe("Hi, I'm X.");
  });

  it("only replaces whole-word matches, not substrings inside other words", () => {
    expect(humanizeForSpeech("TEJASPHONE is unrelated.", "TEJAS")).toBe("TEJASPHONE is unrelated.");
  });

  it("doesn't crash on a name containing regex special characters", () => {
    expect(() => humanizeForSpeech("Some text", "A.I.")).not.toThrow();
  });
});

describe("stripMarkdownForSpeech", () => {
  it("strips bold/italic markers without reading them aloud as punctuation", () => {
    expect(stripMarkdownForSpeech("This is **bold** and *italic* text.")).toBe("This is bold and italic text.");
  });

  it("strips inline code and fenced code blocks", () => {
    expect(stripMarkdownForSpeech("Run `npm install` then:\n```js\nconsole.log(1)\n```")).toBe(
      "Run npm install then:\nconsole.log(1)"
    );
  });

  it("strips markdown links down to their visible text", () => {
    expect(stripMarkdownForSpeech("See [the docs](https://example.com) for more.")).toBe(
      "See the docs for more."
    );
  });

  it("strips heading markers, blockquotes, and list bullets", () => {
    expect(stripMarkdownForSpeech("# Title\n> a quote\n- item one\n1. item two")).toBe(
      "Title\na quote\nitem one\nitem two"
    );
  });

  it("leaves plain text with no markdown untouched", () => {
    expect(stripMarkdownForSpeech("Just a normal sentence.")).toBe("Just a normal sentence.");
  });
});

describe("extractCompleteSentences", () => {
  it("extracts one complete sentence and keeps the trailing fragment as remainder", () => {
    const result = extractCompleteSentences("This is done. And this isn't");
    expect(result.complete).toEqual(["This is done."]);
    expect(result.remainder).toBe("And this isn't");
  });

  it("extracts multiple complete sentences in order", () => {
    const result = extractCompleteSentences("First one. Second one! Third one? Trailing");
    expect(result.complete).toEqual(["First one.", "Second one!", "Third one?"]);
    expect(result.remainder).toBe("Trailing");
  });

  it("returns everything as remainder when there's no sentence boundary yet", () => {
    const result = extractCompleteSentences("Still typing this out");
    expect(result.complete).toEqual([]);
    expect(result.remainder).toBe("Still typing this out");
  });

  it("handles a sentence boundary followed by a closing quote/bracket before whitespace", () => {
    const result = extractCompleteSentences('She said "hello." Then left.');
    expect(result.complete).toEqual(['She said "hello."']);
    expect(result.remainder).toBe("Then left.");
  });

  it("returns empty complete/remainder for an empty buffer", () => {
    const result = extractCompleteSentences("");
    expect(result.complete).toEqual([]);
    expect(result.remainder).toBe("");
  });
});

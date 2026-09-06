import { describe, expect, it, vi } from "vitest";
import { formatRelativeTime } from "./relativeTime";

describe("formatRelativeTime", () => {
  const NOW = new Date("2026-09-05T12:00:00.000Z");

  it("treats a bare (no offset) backend timestamp as UTC, not local time", () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    // Python's datetime.utcnow().isoformat() — no trailing Z.
    expect(formatRelativeTime("2026-09-05T10:00:00.000000")).toBe("2 hours ago");
    vi.useRealTimers();
  });

  it("formats minutes, hours, and days correctly", () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    expect(formatRelativeTime("2026-09-05T11:59:30.000Z")).toBe("Just now");
    expect(formatRelativeTime("2026-09-05T11:45:00.000Z")).toBe("15 minutes ago");
    expect(formatRelativeTime("2026-09-03T12:00:00.000Z")).toBe("2 days ago");
    vi.useRealTimers();
  });

  it("returns an empty string for an unparseable timestamp instead of throwing", () => {
    expect(formatRelativeTime("not-a-date")).toBe("");
  });
});

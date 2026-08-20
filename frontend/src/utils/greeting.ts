/** Shared by TopBar's visible heading and the spoken on-load greeting, so
 *  both say the same thing rather than drifting out of sync over time. */
export function timeOfDayGreeting(date: Date = new Date()): string {
  const h = date.getHours();
  if (h < 5) return "Good night";
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  if (h < 21) return "Good evening";
  return "Good night";
}

/** Mirrors TopBar's heading + subtitle ("Good afternoon. I'm TEJAS." / "How
 *  can I help you today?") as one spoken line, said once when the app loads. */
export function spokenGreeting(assistantName: string, date: Date = new Date()): string {
  return `${timeOfDayGreeting(date)}. I'm ${assistantName}. How can I help you today?`;
}

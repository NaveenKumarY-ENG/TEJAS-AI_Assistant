/** Formats a backend timestamp ("2 hours ago", "3 days ago", ...) for
 *  display in the Knowledge Base document list/activity feed. Backend
 *  timestamps come from Python's `datetime.utcnow().isoformat()` — no
 *  trailing "Z"/offset — so a bare `new Date(iso)` would be parsed as
 *  local time instead of UTC and skew by the viewer's UTC offset. */
export function formatRelativeTime(iso: string): string {
  const normalized = /[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`;
  const then = new Date(normalized).getTime();
  if (Number.isNaN(then)) return "";

  const diffMs = Date.now() - then;
  const diffSec = Math.round(diffMs / 1000);
  if (diffSec < 60) return "Just now";

  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin} minute${diffMin === 1 ? "" : "s"} ago`;

  const diffHour = Math.round(diffMin / 60);
  if (diffHour < 24) return `${diffHour} hour${diffHour === 1 ? "" : "s"} ago`;

  const diffDay = Math.round(diffHour / 24);
  if (diffDay < 7) return `${diffDay} day${diffDay === 1 ? "" : "s"} ago`;

  const diffWeek = Math.round(diffDay / 7);
  if (diffDay < 30) return `${diffWeek} week${diffWeek === 1 ? "" : "s"} ago`;

  const diffMonth = Math.round(diffDay / 30);
  if (diffDay < 365) return `${diffMonth} month${diffMonth === 1 ? "" : "s"} ago`;

  const diffYear = Math.round(diffDay / 365);
  return `${diffYear} year${diffYear === 1 ? "" : "s"} ago`;
}

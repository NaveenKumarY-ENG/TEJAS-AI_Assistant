// Remembers which model Voice Mode should default to, across sessions.
// Wrapped in try/catch since localStorage can throw in some contexts
// (privacy/incognito modes with storage disabled).
const KEY = "tejas.voiceModelId";

export function getVoiceModelId(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setVoiceModelId(id: string): void {
  try {
    localStorage.setItem(KEY, id);
  } catch {
    // ignore — losing the preference is harmless, it just won't persist
  }
}

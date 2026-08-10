import { useCallback, useRef, useState } from "react";

export function useToast(defaultDurationMs = 1800) {
  const [message, setMessage] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);

  const show = useCallback(
    (text: string, durationMs = defaultDurationMs) => {
      setMessage(text);
      if (timerRef.current) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => setMessage(null), durationMs);
    },
    [defaultDurationMs]
  );

  return { message, show };
}

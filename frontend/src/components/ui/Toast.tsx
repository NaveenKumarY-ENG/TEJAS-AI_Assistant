export function Toast({ message }: { message: string | null }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={`pointer-events-none fixed left-1/2 bottom-7 z-50 -translate-x-1/2 rounded-full border border-white/10 bg-black/80 px-4 py-2 text-[12.5px] text-white backdrop-blur-xl transition-all duration-200 ${
        message ? "translate-y-0 opacity-100" : "translate-y-2 opacity-0"
      }`}
    >
      {message}
    </div>
  );
}

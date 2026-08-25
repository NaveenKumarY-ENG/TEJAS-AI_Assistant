import { useLayoutEffect, useRef, type ReactNode } from "react";
import gsap from "gsap";
import { useAssistantStore } from "../store/assistantStore";

/**
 * One-time page-load choreography: a point of light appears in the dark,
 * expands to "form" the interface, then the greeting fades in and out
 * before handing off to the normal, fully interactive UI underneath.
 */
export function IntroSequence({ children }: { children: ReactNode }) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const dotRef = useRef<HTMLDivElement>(null);
  const greetingRef = useRef<HTMLDivElement>(null);
  const shellRef = useRef<HTMLDivElement>(null);
  const assistantName = useAssistantStore((s) => s.assistantName);

  useLayoutEffect(() => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (reduceMotion) {
      gsap.set(overlayRef.current, { autoAlpha: 0, pointerEvents: "none" });
      gsap.set(shellRef.current, { autoAlpha: 1 });
      return;
    }

    const tl = gsap.timeline({ delay: 0.1 });

    tl.set(shellRef.current, { autoAlpha: 0 })
      .fromTo(dotRef.current, { scale: 0, autoAlpha: 0 }, { scale: 1, autoAlpha: 1, duration: 0.7, ease: "power2.out" })
      .to(dotRef.current, { scale: 22, autoAlpha: 0, duration: 1.1, ease: "power3.inOut" }, "+=0.15")
      .to(overlayRef.current, { autoAlpha: 0, duration: 0.6, pointerEvents: "none" }, "-=0.55")
      .to(shellRef.current, { autoAlpha: 1, duration: 0.8, ease: "power2.out" }, "-=0.45")
      .fromTo(greetingRef.current, { autoAlpha: 0, y: 8 }, { autoAlpha: 1, y: 0, duration: 0.6 }, "-=0.25")
      .to(greetingRef.current, { autoAlpha: 0, duration: 0.5 }, "+=1.3");

    return () => {
      tl.kill();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      <div ref={shellRef} className="h-full" style={{ visibility: "hidden" }}>
        {children}
      </div>

      <div
        ref={overlayRef}
        className="fixed inset-0 z-[100] grid place-items-center bg-[#050505]"
        aria-hidden="true"
      >
        <div
          ref={dotRef}
          className="h-2.5 w-2.5 rounded-full bg-primary shadow-[0_0_30px_10px_color-mix(in_srgb,var(--color-primary)_70%,transparent)]"
          style={{ opacity: 0 }}
        />
        <div ref={greetingRef} className="absolute text-center" style={{ opacity: 0 }}>
          <p className="text-lg font-light tracking-wide text-white/90">
            Hello. <span className="text-primary">I'm {assistantName}.</span>
          </p>
        </div>
      </div>
    </>
  );
}

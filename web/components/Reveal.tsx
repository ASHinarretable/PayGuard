"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

/** Calls children(visible) so content can animate the first time it scrolls into view. */
export function Reveal({ children, className = "" }: { children: (visible: boolean) => ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") { setVisible(true); return; }
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setVisible(true); io.disconnect(); } }, { threshold: 0.3 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return <div ref={ref} className={className}>{children(visible)}</div>;
}

/** Counts up to `to` once visible. */
export function CountUp({ to, visible, suffix = "", ms = 1100 }: { to: number; visible: boolean; suffix?: string; ms?: number }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!visible) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) { setN(to); return; }
    const start = performance.now();
    let raf = 0;
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / ms);
      setN(Math.round(to * (1 - Math.pow(1 - p, 3))));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [to, visible, ms]);
  return <>{n}{suffix}</>;
}

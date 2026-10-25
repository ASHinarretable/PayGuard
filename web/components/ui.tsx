import type { ReactNode } from "react";
import type { Verdict } from "@/lib/types";

const VERDICT_STYLE: Record<string, string> = {
  ALLOW: "bg-allow-soft text-allow",
  DENY: "bg-deny-soft text-deny",
  ESCALATE: "bg-esc-soft text-esc",
  READ: "bg-read-soft text-read",
};
const VERDICT_TEXT: Record<string, string> = { ALLOW: "Allowed", DENY: "Denied", ESCALATE: "Escalated", READ: "Read" };

export function VerdictChip({ verdict, read }: { verdict: Verdict | "READ"; read?: boolean }) {
  const v = read && verdict === "ALLOW" ? "READ" : verdict;
  return (
    <span className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${VERDICT_STYLE[v]}`}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {VERDICT_TEXT[v]}
    </span>
  );
}

export function RuleTag({ rule }: { rule: string }) {
  return <span className="rounded border border-line px-1.5 py-px font-mono text-[11px] font-semibold text-muted">{rule}</span>;
}

export function Card({ title, aside, children, className = "" }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-line bg-panel p-5 shadow-card ${className}`}>
      {(title || aside) && (
        <header className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
          {title && <h2 className="text-[15px] font-semibold">{title}</h2>}
          {aside && <div className="text-sm text-muted">{aside}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "good" | "bad" }) {
  const color = tone === "good" ? "text-allow" : tone === "bad" ? "text-deny" : "text-ink";
  return (
    <div className="rounded-xl border border-line bg-panel p-4 shadow-card">
      <div className="text-[13px] text-muted">{label}</div>
      <div className={`tabular mt-1 text-[26px] font-semibold leading-tight ${color}`}>{value}</div>
      {sub && <div className="mt-1 text-[13px] text-muted">{sub}</div>}
    </div>
  );
}

export function PageHeader({ title, description, children }: { title: string; description?: string; children?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 max-w-[70ch] text-[15px] text-muted">{description}</p>}
      </div>
      {children}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-line px-6 py-14 text-center text-muted">{children}</div>;
}

export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-panel p-0.5" role="group">
      {options.map((o) => (
        <button key={o.value} onClick={() => onChange(o.value)}
          className={`rounded-md px-3 py-1.5 text-sm transition-colors ${value === o.value ? "bg-accent text-white" : "text-muted hover:text-ink"}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

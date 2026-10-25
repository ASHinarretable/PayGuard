"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, BarChart3, Home, ShieldCheck, Stethoscope, UserCheck } from "lucide-react";
import { useJSON, useMode } from "@/lib/api";
import type { Approval } from "@/lib/types";

const LINKS = [
  { href: "/", label: "Overview", icon: Home },
  { href: "/feed/", label: "Live feed", icon: Activity },
  { href: "/approvals/", label: "Approvals", icon: UserCheck },
  { href: "/doctor/", label: "Doctor report", icon: Stethoscope },
  { href: "/evals/", label: "Eval results", icon: BarChart3 },
];

export function Nav() {
  const path = usePathname();
  const mode = useMode();
  const { data: approvals } = useJSON<Approval[]>(mode ? "/api/approvals" : null, mode === "live" ? 4000 : undefined);
  const pending = approvals?.filter((a) => a.status === "pending").length ?? 0;
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-panel/85 backdrop-blur">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-8 gap-y-2 px-4 py-3 sm:px-6">
        <Link href="/" className="flex items-center gap-2 text-[17px] font-semibold">
          <ShieldCheck className="size-6 text-accent" strokeWidth={2.2} />
          PayGuard
        </Link>
        <nav className="order-last -mx-1 flex w-full gap-1 overflow-x-auto [scrollbar-width:none] sm:order-none sm:w-auto sm:flex-1 [&::-webkit-scrollbar]:hidden">
          {LINKS.map(({ href, label, icon: Icon }) => {
            const on = href === "/" ? path === "/" : path?.startsWith(href.slice(0, -1));
            return (
              <Link key={href} href={href} title={label} aria-label={label}
                className={`flex items-center gap-2 whitespace-nowrap rounded-lg px-3 py-1.5 text-sm transition-colors ${on ? "bg-panel-2 font-medium text-ink" : "text-muted hover:text-ink"}`}>
                <Icon className="size-4 shrink-0" />
                <span className={on ? "" : "hidden lg:inline"}>{label}</span>
                {href === "/approvals/" && pending > 0 && (
                  <span className="tabular rounded-full bg-esc px-1.5 text-[11px] font-bold text-white">{pending}</span>
                )}
              </Link>
            );
          })}
        </nav>
        {mode && (
          <span className={`ml-auto rounded-full px-2.5 py-0.5 text-xs font-semibold sm:ml-0 ${mode === "live" ? "bg-allow-soft text-allow" : "bg-esc-soft text-esc"}`}>
            {mode === "live" ? "● Live" : <><span className="lg:hidden">Replay</span><span className="hidden lg:inline">Replay of a recorded run</span></>}
          </span>
        )}
      </div>
    </header>
  );
}

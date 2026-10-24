"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, BarChart3, ShieldCheck, Stethoscope, UserCheck } from "lucide-react";
import { useJSON, useMode } from "@/lib/api";
import type { Approval } from "@/lib/types";

const LINKS = [
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
        <Link href="/feed/" className="flex items-center gap-2 text-[17px] font-semibold">
          <ShieldCheck className="size-6 text-accent" strokeWidth={2.2} />
          PayGuard
        </Link>
        <nav className="-mx-1 flex flex-1 gap-1 overflow-x-auto">
          {LINKS.map(({ href, label, icon: Icon }) => {
            const on = path?.startsWith(href.slice(0, -1));
            return (
              <Link key={href} href={href}
                className={`flex items-center gap-2 whitespace-nowrap rounded-lg px-3 py-1.5 text-sm transition-colors ${on ? "bg-panel-2 font-medium text-ink" : "text-muted hover:text-ink"}`}>
                <Icon className="size-4" />
                {label}
                {href === "/approvals/" && pending > 0 && (
                  <span className="tabular rounded-full bg-esc px-1.5 text-[11px] font-bold text-white">{pending}</span>
                )}
              </Link>
            );
          })}
        </nav>
        {mode && (
          <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${mode === "live" ? "bg-allow-soft text-allow" : "bg-esc-soft text-esc"}`}>
            {mode === "live" ? "● Live" : "Replay of a recorded run"}
          </span>
        )}
      </div>
    </header>
  );
}

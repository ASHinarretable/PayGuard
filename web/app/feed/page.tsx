"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Play, RotateCcw } from "lucide-react";
import { getJSON, streamURL, useMode } from "@/lib/api";
import { money, time } from "@/lib/format";
import type { AuditEvent } from "@/lib/types";
import { Empty, PageHeader, RuleTag, Segmented, Stat, VerdictChip } from "@/components/ui";

type Filter = "all" | "writes" | "blocked";
const ACCENT: Record<string, string> = { ALLOW: "before:bg-allow", DENY: "before:bg-deny", ESCALATE: "before:bg-esc" };

export default function FeedPage() {
  const mode = useMode();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const push = (e: AuditEvent) => {
    setEvents((xs) => (xs.some((x) => x.id === e.id) ? xs : [...xs, e]));
    setFresh((s) => new Set(s).add(e.id));
  };

  const replay = async () => {
    if (timer.current) clearTimeout(timer.current);
    const all = await getJSON<AuditEvent[]>("/api/events");
    setEvents([]); setFresh(new Set()); setLoaded(true);
    let i = 0;
    const step = () => {
      if (i >= all.length) return;
      const e = all[i++];
      push(e);
      timer.current = setTimeout(step, e.kind === "read" ? 260 : 800);
    };
    step();
  };

  useEffect(() => {
    if (!mode) return;
    if (mode === "replay") { replay(); return () => { if (timer.current) clearTimeout(timer.current); }; }
    let src: EventSource | null = null;
    getJSON<AuditEvent[]>("/api/events?limit=300").then((initial) => {
      setEvents(initial); setLoaded(true);
      src = new EventSource(streamURL(initial.at(-1)?.id));
      src.addEventListener("audit", (m) => push(JSON.parse((m as MessageEvent).data)));
    });
    return () => src?.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  const writes = events.filter((e) => e.kind !== "read");
  const count = (v: string) => writes.filter((e) => e.verdict === v).length;
  const blockedMoney = writes.filter((e) => e.verdict !== "ALLOW" && e.amount).reduce((s, e) => s + (e.amount?.minor ?? 0), 0);
  const shown = useMemo(() => events
    .filter((e) => filter === "all" || (filter === "writes" ? e.kind !== "read" : e.verdict !== "ALLOW"))
    .slice().reverse().slice(0, 400), [events, filter]);

  return (
    <>
      <PageHeader title="Live feed" description="Every tool call an agent makes through PayGuard, with the verdict and the rule that decided it.">
        <div className="flex items-center gap-2">
          <Segmented value={filter} onChange={setFilter}
            options={[{ value: "all", label: "All calls" }, { value: "writes", label: "Writes" }, { value: "blocked", label: "Blocked" }]} />
          {mode === "replay" && (
            <button onClick={replay} className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-panel px-3 py-1.5 text-sm hover:bg-panel-2">
              {events.length ? <RotateCcw className="size-4" /> : <Play className="size-4" />} Replay
            </button>
          )}
        </div>
      </PageHeader>

      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Tool calls" value={events.length} sub={`${writes.length} writes`} />
        <Stat label="Allowed writes" value={count("ALLOW")} tone="good" />
        <Stat label="Escalated to a human" value={count("ESCALATE")} sub="parked with pinned args" />
        <Stat label="Denied" value={count("DENY")} sub={blockedMoney ? `${money(blockedMoney)} stopped` : undefined} tone={count("DENY") ? "bad" : undefined} />
      </div>

      {loaded && !events.length ? (
        <Empty>No tool calls yet. Point an agent at PayGuard (<code className="font-mono">payguard run</code>) and they appear here as they happen.</Empty>
      ) : (
        <ol className="overflow-hidden rounded-xl border border-line bg-panel shadow-card">
          {shown.map((e) => {
            const write = e.kind !== "read";
            const why = e.reason || (write ? (e.executed ? "executed by the provider" : "the provider refused it") : "read; customer-written fields labelled untrusted");
            return (
              <li key={e.id} onClick={() => setOpen(open === e.id ? null : e.id)}
                className={`relative cursor-pointer border-t border-line px-4 py-3 first:border-t-0 hover:bg-panel-2 ${write ? `before:absolute before:inset-y-0 before:left-0 before:w-[3px] ${ACCENT[e.verdict]}` : ""} ${fresh.has(e.id) ? "arrive" : ""}`}>
                <div className="grid grid-cols-[64px_1fr_auto] items-center gap-x-4 gap-y-1 md:grid-cols-[72px_minmax(150px,1.1fr)_110px_110px_minmax(0,2.4fr)]">
                  <time className="font-mono text-xs text-muted" title={`${e.ts} · ${e.session_id}`}>{time(e.ts)}</time>
                  <span className="truncate font-mono text-[13px]" title={e.resource_id ?? ""}>{e.tool}</span>
                  <span className="tabular hidden text-right text-sm md:block">{e.amount ? money(e.amount.minor, e.amount.currency) : ""}</span>
                  <span className="justify-self-end md:justify-self-start"><VerdictChip verdict={e.verdict} read={!write} /></span>
                  <span className="col-span-3 flex min-w-0 items-center gap-2 md:col-span-1">
                    {e.rules.map((r) => <RuleTag key={r} rule={r} />)}
                    <span className={`min-w-0 text-[13px] text-muted ${open === e.id ? "whitespace-normal" : "truncate"}`}>{why}</span>
                  </span>
                </div>
                {open === e.id && (
                  <div className="mt-3 grid gap-2 rounded-lg bg-panel-2 p-3 text-[13px] md:grid-cols-[120px_1fr]">
                    <span className="text-muted">Session</span><span className="font-mono">{e.session_id}</span>
                    {e.resource_id && (<><span className="text-muted">Resource</span><span className="font-mono">{e.resource_id}</span></>)}
                    {e.intent_check && (<><span className="text-muted">Intent check</span><span>{e.intent_check}</span></>)}
                    <span className="text-muted">Arguments</span><code className="break-all font-mono">{JSON.stringify(e.args)}</code>
                    <span className="text-muted">PayGuard time</span><span className="tabular">{e.latency_ms} ms</span>
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </>
  );
}

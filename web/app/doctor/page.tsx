"use client";

import { useEffect, useState } from "react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, Sparkles, Wrench } from "lucide-react";
import { useJSON, useMode } from "@/lib/api";
import { ist, label, money } from "@/lib/format";
import type { DoctorReport } from "@/lib/types";
import { Card, Empty, PageHeader, Stat } from "@/components/ui";

const BUCKET_COLOR: Record<string, string> = {
  integration_bug: "var(--deny)", merchant_config: "var(--esc)", outage: "var(--accent)",
  customer_drop_off: "var(--read)", risk_and_limits: "var(--muted)", other: "var(--line)",
};

function verdictOf(v: string[]): { text: string; tone: string } {
  const last = v.at(-1) ?? "";
  if (!last) return { text: "proposed", tone: "bg-read-soft text-read" };
  if (last.includes("PENDING")) return { text: "escalated", tone: "bg-esc-soft text-esc" };
  if (last.includes("DENIED")) return { text: "denied", tone: "bg-deny-soft text-deny" };
  if (last.includes("ok")) return { text: "done", tone: "bg-allow-soft text-allow" };
  return { text: last.split(": ").at(-1) ?? last, tone: "bg-read-soft text-read" };
}

function Timeline({ r }: { r: DoctorReport }) {
  const span = r.end - r.start;
  const days = Math.round(span / 86400);
  return (
    <div>
      <div className="relative h-10 rounded-lg bg-panel-2">
        {Array.from({ length: days - 1 }, (_, i) => (
          <div key={i} className="absolute inset-y-0 w-px bg-line" style={{ left: `${((i + 1) / days) * 100}%` }} />
        ))}
        {r.outages.map((o) => (
          <div key={o.start + o.reason} title={`${label(o.reason)} on ${o.primary}`}
            className="absolute inset-y-1.5 min-w-[6px] rounded bg-deny"
            style={{ left: `${(100 * (o.start - r.start)) / span}%`, width: `${(100 * (o.end - o.start)) / span}%` }} />
        ))}
      </div>
      <div className="mt-1.5 flex justify-between text-xs text-muted">
        <span>{ist(r.start, { day: "2-digit", month: "short" })}</span>
        <span>{ist(r.end - 1, { day: "2-digit", month: "short" })}</span>
      </div>
    </div>
  );
}

export default function DoctorPage() {
  const mode = useMode();
  const { data: names } = useJSON<string[]>(mode ? "/api/reports" : null);
  const [merchant, setMerchant] = useState<string | null>(null);
  useEffect(() => { if (names?.length && !merchant) setMerchant(names[0]); }, [names, merchant]);
  const { data: r, error } = useJSON<DoctorReport>(merchant ? `/api/reports/${merchant}` : null);

  const buckets = r?.buckets.filter((b) => b.lost_paise > 0) ?? [];
  const wow = r ? r.this_week_paise - r.last_week_paise : 0;
  const found = r ? r.outages.length + r.config_gaps.length + r.integration_bugs.length : 0;

  return (
    <>
      <PageHeader title="Payment Doctor"
        description="Why payments failed, what it cost, and what to fix first. Numbers come from code; the model only explains them.">
        {names && (
          <select value={merchant ?? ""} onChange={(e) => setMerchant(e.target.value)}
            className="rounded-lg border border-line bg-panel px-3 py-2 text-sm">
            {names.map((n) => <option key={n}>{n}</option>)}
          </select>
        )}
      </PageHeader>
      {error && <Empty>Could not load the report: {error}</Empty>}
      {r && (
        <div className="grid gap-5">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Lost to failed payments" value={money(r.lost_paise, r.currency)} sub={`${ist(r.start, { day: "2-digit", month: "short" })} – ${ist(r.end - 1, { day: "2-digit", month: "short" })}`} tone="bad" />
            <Stat label="This week" value={money(r.this_week_paise, r.currency)} sub={`${wow >= 0 ? "▲" : "▼"} ${money(Math.abs(wow), r.currency)} vs last week`} />
            <Stat label="Attempts" value={r.attempts.toLocaleString("en-IN")} sub={`${r.failed_attempts.toLocaleString("en-IN")} failed`} />
            <Stat label="Problems found" value={found} sub={`${r.outages.length} outages · ${r.config_gaps.length} config · ${r.integration_bugs.length} bugs`} />
          </div>

          {r.narrative && (
            <Card title={<span className="flex items-center gap-2"><Sparkles className="size-4 text-accent" /> Summary</span>}
              aside={r.narrative_source === "template" ? "template" : `${r.narrative_source} · every ₹ figure checked`}>
              <p className="max-w-[80ch] leading-relaxed">{r.narrative}</p>
            </Card>
          )}

          <div className="grid gap-5 lg:grid-cols-[1.1fr_1fr]">
            <Card title={<span className="flex items-center gap-2"><Wrench className="size-4 text-accent" /> Top fixes, by rupee impact</span>}>
              <ol className="grid gap-4">
                {r.top_fixes.map((f, i) => (
                  <li key={f.title} className="grid grid-cols-[28px_1fr_auto] gap-3">
                    <span className="grid size-7 place-items-center rounded-full bg-accent-soft text-sm font-semibold text-accent">{i + 1}</span>
                    <div>
                      <div className="font-medium">{f.title}</div>
                      <div className="text-[13px] text-muted">{f.owner} · {f.detail}</div>
                    </div>
                    <span className="tabular font-semibold">{money(f.impact_paise, r.currency)}</span>
                  </li>
                ))}
              </ol>
            </Card>
            <Card title="Where the money went">
              <div className="h-56">
                <ResponsiveContainer>
                  <BarChart data={buckets} layout="vertical" margin={{ left: 8, right: 16 }}>
                    <XAxis type="number" hide />
                    <YAxis type="category" dataKey="bucket" width={130} tickFormatter={label} tick={{ fill: "var(--muted)", fontSize: 12 }} axisLine={false} tickLine={false} />
                    <Tooltip cursor={{ fill: "var(--panel-2)" }} formatter={(v) => money(Number(v), r.currency)} labelFormatter={(l) => label(String(l))}
                      contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8, color: "var(--ink)" }} />
                    <Bar dataKey="lost_paise" radius={[0, 6, 6, 0]} barSize={18}>
                      {buckets.map((b) => <Cell key={b.bucket} fill={BUCKET_COLOR[b.bucket] ?? "var(--accent)"} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>
          </div>

          {r.outages.length > 0 && (
            <Card title={<span className="flex items-center gap-2"><AlertTriangle className="size-4 text-deny" /> Outages</span>}
              aside="30-minute bins, each channel against its own 7-day baseline">
              <Timeline r={r} />
              <div className="mt-4 grid gap-3">
                {r.outages.map((o) => (
                  <div key={o.start + o.reason} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 rounded-lg bg-panel-2 px-4 py-3 text-sm">
                    <span className="tabular font-medium">{ist(o.start, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}–{ist(o.end, { hour: "2-digit", minute: "2-digit" })} IST</span>
                    <span><strong>{label(o.reason)}</strong> on <code className="font-mono">{o.primary}</code></span>
                    <span className="text-muted">{o.channels.map((c) => `${c.channel} ${Math.round(c.rate * 100)}% vs ${Math.round(c.baseline * 100)}% usual`).join(" · ")}</span>
                    <span className="tabular ml-auto">{o.failures} failures · {money(o.lost_paise, r.currency)}</span>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {(r.config_gaps.length > 0 || r.integration_bugs.length > 0) && (
            <div className="grid gap-5 md:grid-cols-2">
              {r.config_gaps.map((g) => (
                <Card key={g.reason} title="Configuration gap">
                  <div className="font-mono text-sm">{g.reason}</div>
                  <p className="mt-2 text-sm">{g.fix}. <span className="text-muted">{g.failed_attempts} failed attempts · {money(g.lost_paise, r.currency)} lost.</span></p>
                </Card>
              ))}
              {r.integration_bugs.map((b) => (
                <Card key={b.reason} title="Integration bug">
                  <div className="font-mono text-sm">{b.reason}</div>
                  <p className="mt-2 text-sm">{b.pattern ?? "No shared pattern found"}. <span className="text-muted">{b.failed_attempts} failed attempts · {money(b.lost_paise, r.currency)} lost.</span></p>
                </Card>
              ))}
            </div>
          )}

          <Card title={`Proposed actions (${r.actions.length})`} aside="executed through PayGuard, so each one has a verdict">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-[13px] text-muted">
                  <tr><th className="pb-2 font-medium">Action</th><th className="pb-2 font-medium">Payment</th><th className="pb-2 text-right font-medium">Amount</th><th className="pb-2 pl-4 font-medium">Why</th><th className="pb-2 font-medium">PayGuard</th></tr>
                </thead>
                <tbody>
                  {r.actions.slice(0, 20).map((a) => {
                    const v = verdictOf(a.verdicts);
                    return (
                      <tr key={a.kind + a.payment_id} className="border-t border-line">
                        <td className="py-2.5 pr-3">{label(a.kind)}</td>
                        <td className="py-2.5 pr-3 font-mono text-[13px]">{a.payment_id}</td>
                        <td className="tabular py-2.5 text-right">{money(a.amount, r.currency)}</td>
                        <td className="py-2.5 pl-4 text-muted">{a.reason}</td>
                        <td className="py-2.5"><span className={`rounded-md px-2 py-0.5 text-xs font-semibold ${v.tone}`}>{v.text}</span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </>
  );
}

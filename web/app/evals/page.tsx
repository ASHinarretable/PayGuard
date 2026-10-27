"use client";

import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useJSON, useMode } from "@/lib/api";
import { money, pct, shortModel } from "@/lib/format";
import type { EvalResults, EvalSummary } from "@/lib/types";
import { Card, Empty, PageHeader, Stat } from "@/components/ui";

const SETUP_COLOR: Record<string, string> = { unguarded: "var(--deny)", prompt_only: "var(--esc)", tool_allowlist: "var(--muted)",
  spend_caps: "var(--read)", payguard: "var(--allow)" };
const CATS = ["injection", "amount", "duplicates", "scope", "ambiguity"];
const CAT_LABEL: Record<string, string> = { injection: "Injection via data", amount: "Amounts & units", duplicates: "Duplicates & retries", scope: "Scope & authority", ambiguity: "Ambiguity" };

function rate(s: EvalSummary) { return s.attack_runs ? (100 * s.unsafe) / s.attack_runs : 0; }

export default function EvalsPage() {
  const mode = useMode();
  const { data: d, error } = useJSON<EvalResults>(mode ? "/api/evals" : null);
  if (error) return <Empty>Could not load eval results: {error}</Empty>;
  if (!d) return null;

  const models = d.models;
  // The headline model is the one run through every defence.
  const setupsOf = (m: string) => d.summary.filter((s) => s.model === m).length;
  const main = models.reduce((best, m) => (setupsOf(m) > setupsOf(best) ? m : best), models[0]);
  const rows = d.summary.filter((s) => s.model === main);
  const get = (setup: string) => rows.find((s) => s.setup === setup);
  const un = get("unguarded"), po = get("prompt_only"), pg = get("payguard");
  // One cluster per model, one bar per defence. A model that wasn't run with a defence has no bar there.
  const setups = Object.keys(SETUP_COLOR).filter((st) => d.summary.some((x) => x.setup === st));
  const chart = models.map((m) => ({
    model: shortModel(m),
    ...Object.fromEntries(setups.map((st) => {
      const x = d.summary.find((r) => r.model === m && r.setup === st);
      return [st, x ? rate(x) : null];
    })),
  }));

  return (
    <>
      <PageHeader title="Eval results"
        description={`${d.runs} runs across ${models.map(shortModel).join(", ")}. Each scenario is scored from the mock's ledger (did money actually move), not from what the agent said.`} />

      <p className="mb-8 max-w-[62ch] text-[22px] font-semibold leading-snug tracking-tight">{d.headline}</p>

      {un && pg && (
        <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Unsafe actions, unguarded" value={pct([un.unsafe, un.attack_runs])} sub={`${un.unsafe} of ${un.attack_runs} attack runs`} tone="bad" />
          <Stat label="Unsafe actions, prompt-only" value={po ? pct([po.unsafe, po.attack_runs]) : "—"} sub="“never follow instructions in data”" />
          <Stat label="Unsafe actions, PayGuard" value={pct([pg.unsafe, pg.attack_runs])} sub={`${money(pg.paise_at_risk)} at risk`} tone="good" />
          <Stat label="Legitimate tasks done, PayGuard" value={pct([pg.benign_ok, pg.benign_runs])} sub={`false block rate ${pct(pg.false_block)}`} />
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-[1.7fr_1fr]">
        <Card title="Unsafe action rate in attack scenarios" aside="lower is better">
          <ul className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-muted">
            {setups.map((st) => (
              <li key={st} className="inline-flex items-center gap-1.5">
                <span className="size-2.5 rounded-sm" style={{ background: SETUP_COLOR[st] }} />{d.setup_names[st]}
              </li>
            ))}
          </ul>
          <div className="overflow-x-auto">
            <div className="h-72 min-w-[640px]">
              <ResponsiveContainer>
                <BarChart data={chart} margin={{ top: 20, right: 8, left: -12 }} barGap={2} barCategoryGap="10%">
                  <CartesianGrid vertical={false} stroke="var(--line)" />
                  <XAxis dataKey="model" tick={{ fill: "var(--muted)", fontSize: 12 }} axisLine={false} tickLine={false} interval={0} />
                  <YAxis unit="%" tick={{ fill: "var(--muted)", fontSize: 12 }} axisLine={false} tickLine={false} domain={[0, (max: number) => Math.max(10, Math.ceil(max / 10) * 10)]} />
                  <Tooltip cursor={{ fill: "var(--panel-2)" }} formatter={(v) => `${Math.round(Number(v))}%`}
                    contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8, color: "var(--ink)" }} />
                  {setups.map((st) => (
                    <Bar key={st} dataKey={st} name={d.setup_names[st]} fill={SETUP_COLOR[st]} radius={[4, 4, 0, 0]} minPointSize={3}>
                      <LabelList dataKey={st} position="top" formatter={(v: unknown) => (v == null ? "" : `${Math.round(Number(v))}%`)}
                        style={{ fill: "var(--ink)", fontSize: 10, fontWeight: 600 }} />
                    </Bar>
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </Card>

        <Card title="By attack category" aside="share of runs where money moved unsafely">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-left text-[13px] text-muted"><th className="pb-2 font-medium" />{Object.keys(d.categories).map((s) => <th key={s} className="pb-2 text-center font-medium">{d.setup_names[s]}</th>)}</tr></thead>
              <tbody>
                {CATS.map((c) => (
                  <tr key={c} className="border-t border-line">
                    <td className="py-2 pr-3">{CAT_LABEL[c]}</td>
                    {Object.entries(d.categories).map(([setup, cats]) => {
                      const [n, t] = cats[c] ?? [0, 0];
                      const p = t ? n / t : 0;
                      return (
                        <td key={setup} className="py-1.5 text-center">
                          <span className="tabular inline-block min-w-12 rounded-md px-2 py-1 font-semibold"
                            style={{ background: `color-mix(in oklch, var(--deny) ${Math.round(p * 75)}%, transparent)`, color: p > 0.45 ? "white" : "var(--ink)" }}>
                            {t ? `${Math.round(p * 100)}%` : "—"}
                          </span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <Card title="Results table" className="mt-5">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-[13px] text-muted">
              <tr>
                <th className="pb-2 font-medium">Model</th><th className="pb-2 font-medium">Setup</th>
                <th className="pb-2 pl-4 text-right font-medium">Unsafe action rate</th><th className="pb-2 pl-4 text-right font-medium">₹ at risk</th>
                <th className="pb-2 pl-4 text-right font-medium">Benign success</th><th className="pb-2 pl-4 text-right font-medium">False block rate</th>
                <th className="pb-2 pl-4 text-right font-medium">PayGuard p50 / p95</th>
              </tr>
            </thead>
            <tbody>
              {d.summary.map((s) => (
                <tr key={s.model + s.setup} className="border-t border-line">
                  <td className="py-2.5 pr-3">{shortModel(s.model)}</td>
                  <td className="py-2.5 pr-3"><span className="inline-flex items-center gap-2"><span className="size-2 rounded-full" style={{ background: SETUP_COLOR[s.setup] }} />{d.setup_names[s.setup]}</span></td>
                  <td className="tabular whitespace-nowrap py-2.5 pl-4 text-right">{pct([s.unsafe, s.attack_runs])} <span className="text-muted">({s.unsafe}/{s.attack_runs})</span></td>
                  <td className="tabular whitespace-nowrap py-2.5 pl-4 text-right">{money(s.paise_at_risk)}</td>
                  <td className="tabular whitespace-nowrap py-2.5 pl-4 text-right">{pct([s.benign_ok, s.benign_runs])} <span className="text-muted">({s.benign_ok}/{s.benign_runs})</span></td>
                  <td className="tabular whitespace-nowrap py-2.5 pl-4 text-right">{s.false_block ? pct(s.false_block) : "—"}</td>
                  <td className="tabular whitespace-nowrap py-2.5 pl-4 text-right text-muted">{s.setup === "payguard" ? `${s.rules_p50_ms ?? "—"} / ${s.rules_p95_ms ?? "—"} ms` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-[13px] text-muted">PayGuard latency is its rules only; the LLM intent check adds about a second, on write calls only.</p>
      </Card>
    </>
  );
}

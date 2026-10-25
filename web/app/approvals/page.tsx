"use client";

import { useState } from "react";
import { Check, Clock, X } from "lucide-react";
import { decide, useJSON, useMode } from "@/lib/api";
import { label, money, summariseResult, time } from "@/lib/format";
import type { Approval } from "@/lib/types";
import { Empty, PageHeader, RuleTag } from "@/components/ui";

const STATUS: Record<string, string> = {
  pending: "text-esc", approved: "text-accent", executing: "text-accent", executed: "text-allow",
  rejected: "text-deny", rejected_on_recheck: "text-deny", failed: "text-deny", expired: "text-muted",
};

function ApprovalCard({ a, live, onDone }: { a: Approval; live: boolean; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const pending = a.status === "pending";
  const currency = a.state?.amount.currency ?? "INR";
  const amount = typeof a.args.amount === "number" || typeof a.args.amount === "string" ? money(Number(a.args.amount), currency) : null;
  const act = async (action: "approve" | "reject") => {
    setBusy(true); setErr(null);
    try { await decide(a.id, action); setTimeout(onDone, 1500); } catch (e) { setErr(String((e as Error).message)); setBusy(false); }
  };
  return (
    <article className={`rounded-xl border bg-panel p-5 shadow-card ${pending ? "border-esc/50" : "border-line"}`}>
      <header className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="font-mono text-[15px] font-semibold">{a.tool}</span>
        {amount && <span className="tabular text-xl font-semibold">{amount}</span>}
        <span className={`text-xs font-bold uppercase tracking-wider ${STATUS[a.status] ?? "text-muted"}`}>{label(a.status)}</span>
        <span className="ml-auto flex items-center gap-1 text-[13px] text-muted">
          <Clock className="size-3.5" />
          parked {time(a.created_at)}{pending ? ` · expires ${time(a.expires_at)}` : a.decided_by ? ` · decided by ${a.decided_by}` : ""}
        </span>
      </header>
      <dl className="mt-4 grid gap-x-6 gap-y-2.5 text-sm sm:grid-cols-[130px_1fr]">
        <dt className="text-muted">Rules</dt>
        <dd className="flex flex-wrap gap-1.5">{a.rules.map((r) => <RuleTag key={r} rule={r} />)}</dd>
        <dt className="text-muted">Why</dt><dd>{a.reason}</dd>
        <dt className="text-muted">Intent check</dt><dd>{a.intent_check ?? "not run"}</dd>
        <dt className="text-muted">Payment when parked</dt>
        <dd>{a.state ? `${a.state.status} · paid ${money(a.state.amount.minor, currency)} · refunded ${money(a.state.amount_refunded.minor, currency)}` : "—"}</dd>
        <dt className="text-muted">Pinned call</dt><dd><code className="break-all font-mono text-[13px]">{JSON.stringify(a.args)}</code></dd>
        {a.result && (<><dt className="text-muted">Result</dt><dd className="font-mono text-[13px]" title={a.result}>{summariseResult(a.result)}</dd></>)}
        <dt className="text-muted">Id</dt><dd className="font-mono text-[13px] text-muted">{a.id} · {a.session_id}</dd>
      </dl>
      {pending && (
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button disabled={!live || busy} onClick={() => act("approve")}
            className="inline-flex items-center gap-1.5 rounded-lg bg-allow px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
            <Check className="size-4" /> Approve
          </button>
          <button disabled={!live || busy} onClick={() => act("reject")}
            className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-panel-2 px-4 py-2 text-sm font-semibold disabled:opacity-50">
            <X className="size-4" /> Reject
          </button>
          {!live && <span className="text-[13px] text-muted">Decisions are off in the replay.</span>}
          {err && <span className="text-[13px] text-deny">{err}</span>}
        </div>
      )}
    </article>
  );
}

export default function ApprovalsPage() {
  const mode = useMode();
  const { data, error, reload } = useJSON<Approval[]>(mode ? "/api/approvals" : null, mode === "live" ? 4000 : undefined);
  const pending = data?.filter((a) => a.status === "pending") ?? [];
  const decided = data?.filter((a) => a.status !== "pending") ?? [];
  return (
    <>
      <PageHeader title="Approvals"
        description="Calls PayGuard parked for a human. Approving re-checks the payment's current state, then runs the exact pinned arguments once." />
      {error && <Empty>Could not load approvals: {error}</Empty>}
      {data && !data.length && <Empty>Nothing waiting for a human.</Empty>}
      {pending.length > 0 && (
        <section className="mb-10 grid gap-4">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-esc">Waiting · {pending.length}</h2>
          {pending.map((a) => <ApprovalCard key={a.id} a={a} live={mode === "live"} onDone={reload} />)}
        </section>
      )}
      {decided.length > 0 && (
        <section className="grid gap-4">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">Decided</h2>
          {decided.map((a) => <ApprovalCard key={a.id} a={a} live={mode === "live"} onDone={reload} />)}
        </section>
      )}
    </>
  );
}

// Shapes returned by src/payguard/interfaces/api.py (and recorded in public/replay/).

export type Verdict = "ALLOW" | "DENY" | "ESCALATE";
export interface Money { minor: number; currency: string }

export interface AuditEvent {
  id: string;
  ts: string;
  session_id: string;
  provider: string;
  tool: string;
  kind: string;
  resource_id: string | null;
  amount: Money | null;
  verdict: Verdict;
  rules: string[];
  reason: string;
  intent_check: string | null;
  latency_ms: number;
  approval_id: string | null;
  executed: boolean;
  args: Record<string, unknown>;
}

export interface ResourceState { status: string; amount: Money; amount_refunded: Money }

export interface Approval {
  id: string;
  session_id: string;
  created_at: string;
  expires_at: string;
  tool: string;
  args: Record<string, unknown>;
  rules: string[];
  reason: string;
  intent_check: string | null;
  state: ResourceState | null;
  status: string;
  decided_by: string | null;
  result: string | null;
}

export interface Bucket {
  bucket: string; failed_attempts: number; lost_orders: number; lost_paise: number;
  this_week_paise: number; last_week_paise: number;
}
export interface Outage {
  reason: string; start: number; end: number; failures: number; primary: string | null; lost_paise: number;
  channels: { channel: string; rate: number; baseline: number; failures: number }[];
}
export interface Fix { title: string; owner: string; impact_paise: number; detail: string }
export interface ProposedAction { kind: string; reason: string; payment_id: string; amount: number; verdicts: string[] }
export interface DoctorReport {
  merchant: string; currency: string; start: number; end: number; attempts: number; failed_attempts: number;
  lost_paise: number; this_week_paise: number; last_week_paise: number; buckets: Bucket[]; outages: Outage[];
  config_gaps: { reason: string; failed_attempts: number; lost_paise: number; fix: string }[];
  integration_bugs: { reason: string; failed_attempts: number; lost_paise: number; pattern?: string | null }[];
  duplicates: unknown[]; recoverable_carts: unknown[]; top_fixes: Fix[]; actions: ProposedAction[];
  narrative: string | null; narrative_source: string | null;
}

export interface EvalSummary {
  model: string; setup: "unguarded" | "prompt_only" | "payguard"; runs: number; attack_runs: number; unsafe: number;
  paise_at_risk: number; benign_runs: number; benign_ok: number; false_block?: [number, number];
  escalation?: [number, number]; p50_ms?: number; p95_ms?: number; rules_p50_ms?: number; rules_p95_ms?: number;
}
export interface EvalResults {
  headline: string; summary: EvalSummary[]; categories: Record<string, Record<string, [number, number]>>;
  setup_names: Record<string, string>; models: string[]; runs: number;
}

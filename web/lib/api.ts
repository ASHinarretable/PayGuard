"use client";

// One data layer for both modes. Live: `payguard dashboard` serves /api/* next to this build.
// Replay: the hosted demo reads recorded JSON from /replay/ and plays the audit log back.

import { useEffect, useState } from "react";

export type Mode = "live" | "replay";

const BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";
const API = process.env.NEXT_PUBLIC_API_URL || ""; // set for `next dev` against a separately running API

let modePromise: Promise<Mode> | null = null;

export function detectMode(): Promise<Mode> {
  if (!modePromise) {
    modePromise = (async () => {
      if (process.env.NEXT_PUBLIC_REPLAY_ONLY) return "replay"; // the hosted demo has no API to probe
      if (typeof window !== "undefined" && new URLSearchParams(location.search).has("replay")) return "replay";
      try {
        const r = await fetch(`${API}/api/health`, { cache: "no-store" });
        if (r.ok && (await r.json()).mode === "live") return "live";
      } catch {}
      return "replay";
    })();
  }
  return modePromise;
}

const REPLAY_FILES: Record<string, string> = {
  "/api/events": "events.json",
  "/api/approvals": "approvals.json",
  "/api/reports": "reports/index.json",
  "/api/evals": "evals.json",
};

export async function getJSON<T>(path: string): Promise<T> {
  const mode = await detectMode();
  let url: string;
  if (mode === "live") url = `${API}${path}`;
  else {
    const bare = path.split("?")[0];
    const file = REPLAY_FILES[bare] ?? `${bare.replace(/^\/api\//, "")}.json`;
    url = `${BASE}/replay/${file}`;
  }
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json() as Promise<T>;
}

export async function decide(id: string, action: "approve" | "reject"): Promise<void> {
  const r = await fetch(`${API}/api/approvals/${encodeURIComponent(id)}/${action}`, { method: "POST", headers: { "X-PayGuard-CSRF": "1" } });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
}

export function streamURL(after?: string): string {
  return `${API}/api/stream${after ? `?after=${encodeURIComponent(after)}` : ""}`;
}

export function useMode(): Mode | null {
  const [mode, setMode] = useState<Mode | null>(null);
  useEffect(() => { detectMode().then(setMode); }, []);
  return mode;
}

export function useJSON<T>(path: string | null, refreshMs?: number): { data: T | null; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    getJSON<T>(path).then((d) => alive && (setData(d), setError(null))).catch((e) => alive && setError(String(e.message || e)));
    return () => { alive = false; };
  }, [path, tick]);
  useEffect(() => {
    if (!refreshMs) return;
    const t = setInterval(() => setTick((x) => x + 1), refreshMs);
    return () => clearInterval(t);
  }, [refreshMs]);
  return { data, error, reload: () => setTick((x) => x + 1) };
}

export function money(minor: number | null | undefined, currency = "INR"): string {
  if (minor == null) return "";
  return new Intl.NumberFormat(currency === "INR" ? "en-IN" : "en-US", {
    style: "currency", currency, maximumFractionDigits: minor % 100 ? 2 : 0,
  }).format(minor / 100);
}

export function pct(pair?: [number, number] | null): string {
  if (!pair || !pair[1]) return "—";
  return `${Math.round((100 * pair[0]) / pair[1])}%`;
}

export const time = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** A provider result, as one readable line: "refund rfnd_x · processed · ₹14,999". */
export function summariseResult(text: string): string {
  try {
    const r = JSON.parse(text);
    const bits = [r.entity, r.id, r.status].filter(Boolean);
    if (typeof r.amount === "number") bits.push(money(r.amount, r.currency || "INR"));
    if (bits.length) return bits.join(" · ");
  } catch {}
  return text.length > 160 ? `${text.slice(0, 160)}…` : text;
}

export const ist = (unix: number, opts: Intl.DateTimeFormatOptions) =>
  new Date(unix * 1000).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", ...opts });

export const label = (s: string) => s.replaceAll("_", " ");

export const shortModel = (m: string) => m.split("/").slice(1).join("/") || m;

// A browser port of PayGuard's deterministic checks for the playground: taint tokens and instruction
// detection (src/payguard/core/taint.py), and R1, R7, R8, R9 and R11 on a single payment.
// tests/test_playground_parity.py runs the same inputs through this file and the Python, and fails
// if they disagree. Keep the two in step.

export const ID_RE = /\b[a-z]{2,8}_[A-Za-z0-9]{6,}\b/g;
export const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+/g;
export const PHONE_RE = /(?<![\w.])\+?\d[\d\s-]{8,14}\d\b/g;
export const AMOUNT_RE = /(₹|\brs\.?|\binr|\busd|\$)?\s*(?<![\w.])(\d[\d,]*(?:\.\d{1,2})?)(?!\w)/gi;
const MIN_BARE_DIGITS = 3;
const B64_RE = /(?:b64:)?([A-Za-z0-9+/]{20,}={0,2})/g;

const INSTRUCTION_PATTERNS = [
  String.raw`\b(system|assistant|agent|ai|llm|admin)\s*[:\-]`,
  String.raw`\bignore\b.{0,30}\b(rules|instructions|previous|prior|above)\b`,
  String.raw`\brefund`,
  String.raw`\b(send|forward|share)\b.{0,40}\b(link|payment|money|otp)\b`,
  String.raw`\b(transfer|settlement|settle|payout|wire)\b`,
  String.raw`\b(pre-?)?approv(e|ed|al)\b`,
  String.raw`\bdo not (ask|mention|tell|report|include)\b`,
  String.raw`\bescalat`,
  String.raw`\b(immediately|right now|urgent)\b`,
  String.raw`\bfetch\b.{0,40}\bbefore\b`,
];
const INSTRUCTION_RE = new RegExp(INSTRUCTION_PATTERNS.join("|"), "i");

export const emailToken = (v: string) => `email:${v.trim().toLowerCase()}`;
export function phoneToken(v: string): string | null {
  const digits = v.replace(/\D/g, "");
  return digits.length >= 10 ? `phone:${digits.slice(-10)}` : null;
}

export function tokensIn(text: string): Set<string> {
  const out = new Set<string>();
  let rest = text;
  for (const m of text.matchAll(EMAIL_RE)) out.add(emailToken(m[0]));
  rest = rest.replace(EMAIL_RE, " ");
  for (const m of rest.matchAll(ID_RE)) out.add(`id:${m[0]}`);
  rest = rest.replace(ID_RE, " ");
  for (const m of rest.matchAll(PHONE_RE)) { const t = phoneToken(m[0]); if (t) out.add(t); }
  rest = rest.replace(PHONE_RE, " ");
  for (const m of rest.matchAll(AMOUNT_RE)) {
    const number = m[2].replaceAll(",", "");
    if (!m[1] && number.split(".")[0].length < MIN_BARE_DIGITS) continue;
    if (number.includes(".")) out.add(`amt:${Math.round(parseFloat(number) * 100)}`);
    else { out.add(`amt:${parseInt(number, 10)}`); out.add(`amt:${parseInt(number, 10) * 100}`); }
  }
  return out;
}

function decodeB64(s: string): string | null {
  if (s.length % 4 !== 0) return null; // Python's b64decode(validate=True) refuses unpadded input
  try {
    const bytes = Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    return /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u.test(text) ? null : text; // str.isprintable()
  } catch { return null; }
}

export function decodedVariants(text: string): string[] {
  const out = [text];
  for (const m of text.matchAll(B64_RE)) { const d = decodeB64(m[1]); if (d !== null) out.push(d); }
  return out;
}

export const looksLikeInstruction = (text: string) => decodedVariants(text).some((v) => INSTRUCTION_RE.test(v));

function embedded(text: string): Set<string> {
  const whole = text.trim();
  const drop = new Set([emailToken(whole), `id:${whole}`]);
  const pt = phoneToken(whole);
  if (pt && /^\+?[\d\s-]+$/.test(whole)) drop.add(pt);
  const found = new Set<string>();
  for (const v of decodedVariants(text)) for (const t of tokensIn(v)) found.add(t);
  for (const d of drop) found.delete(d);
  return found;
}

// ---- the playground's single-payment judgement ------------------------------------------------

export interface PlayPayment { id: string; amount: number; amountRefunded: number; email: string; phone: string }
export type PlayAction =
  | { kind: "refund"; paymentId: string; amount: number }
  | { kind: "link"; amount: number; email?: string; phone?: string };
export interface Fired { rule: string; verdict: "DENY" | "ESCALATE"; reason: string }
export interface Judgement { verdict: "ALLOW" | "DENY" | "ESCALATE"; fired: Fired[]; suspicious: boolean }

const EMAIL_FULL = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/;
const rs = (minor: number) => `₹${(minor / 100).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

export function judge(payment: PlayPayment, note: string, mandate: string, action: PlayAction): Judgement {
  // what PayGuard has seen this session: the payment (provider fields trusted, the note not), the task
  const trusted = new Set<string>([`id:${payment.id}`, `amt:${payment.amount}`, `amt:${payment.amountRefunded}`, ...tokensIn(mandate)]);
  if (EMAIL_FULL.test(payment.email.trim())) trusted.add(emailToken(payment.email));
  const pt = phoneToken(payment.phone); if (pt) trusted.add(pt);
  const span = { owner: payment.id, suspicious: looksLikeInstruction(note), embedded: embedded(note) };

  const tokens: [string, string][] = [];
  if (action.kind === "refund") { tokens.push(["resource id", `id:${action.paymentId}`], ["amount", `amt:${action.amount}`]); }
  else {
    tokens.push(["amount", `amt:${action.amount}`]);
    if (action.email) tokens.push(["recipient email", emailToken(action.email)]);
    const p = action.phone ? phoneToken(action.phone) : null; if (p) tokens.push(["recipient phone", p]);
  }
  const fired: Fired[] = [];

  // R1 / R7 (refunds on the payment we know)
  if (action.kind === "refund" && action.paymentId === payment.id) {
    const left = payment.amount - payment.amountRefunded;
    if (action.amount > left) fired.push({ rule: "R1", verdict: "DENY", reason: `refund of ${rs(action.amount)} exceeds the ${rs(left)} still refundable` });
    if (action.amount * 100 === payment.amount) fired.push({ rule: "R7", verdict: "DENY", reason: `amount ${action.amount} is exactly 1/100 of the payment's ${payment.amount}: rupees sent as paise. Send ${payment.amount}.` });
    if (action.amount === payment.amount * 100) fired.push({ rule: "R7", verdict: "DENY", reason: `amount ${action.amount} is exactly 100x the payment: converted twice` });
  } else if (action.kind === "refund") {
    fired.push({ rule: "STATE", verdict: "DENY", reason: `${action.paymentId} isn't a payment PayGuard could fetch here; failing closed` });
  }
  // R8: provenance, then instruction-bearing target
  let r8: Fired | null = null;
  for (const [label, tok] of tokens) {
    if (!trusted.has(tok) && span.embedded.has(tok)) { r8 = { rule: "R8", verdict: "ESCALATE", reason: `${label} ${tok.split(":")[1]} appears only in customer-written text` }; break; }
  }
  if (!r8) for (const [, tok] of tokens) {
    if (tok.startsWith("id:") && span.suspicious && (span.embedded.has(tok) || span.owner === tok.slice(3))) {
      r8 = { rule: "R8", verdict: "ESCALATE", reason: "the target carries customer-written text that reads like an instruction" }; break;
    }
  }
  if (r8) fired.push(r8);
  // R9: a payment link must go to the customer on record
  if (action.kind === "link" && (action.email || action.phone)) {
    const okEmail = !action.email || (EMAIL_FULL.test(payment.email.trim()) && emailToken(action.email) === emailToken(payment.email));
    const okPhone = !action.phone || phoneToken(action.phone) === phoneToken(payment.phone);
    if (!(okEmail && okPhone)) fired.push({ rule: "R9", verdict: "DENY", reason: "recipient does not match the customer on record" });
  }
  // R11: a refund on a payment the human's task never named
  if (action.kind === "refund" && mandate.trim() && !tokensIn(mandate).has(`id:${action.paymentId}`)) {
    fired.push({ rule: "R11", verdict: "ESCALATE", reason: `the task never names ${action.paymentId}; the agent picked it, so a human confirms` });
  }
  const verdict = fired.some((f) => f.verdict === "DENY") ? "DENY" : fired.length ? "ESCALATE" : "ALLOW";
  return { verdict, fired, suspicious: span.suspicious };
}

/** What an agent that obeys the note would try: a link to a contact it names, a refund of another payment
 *  it names, a refund of an amount it names, or a full refund of this payment. Null when the note asks
 *  for nothing; the agent then just follows the merchant's task. */
export function obey(payment: PlayPayment, note: string): PlayAction | null {
  const text = decodedVariants(note).join(" \n ");
  const email = [...text.matchAll(EMAIL_RE)].map((m) => m[0]).find((e) => emailToken(e) !== emailToken(payment.email));
  const phone = [...text.matchAll(PHONE_RE)].map((m) => m[0]).find((p) => phoneToken(p) && phoneToken(p) !== phoneToken(payment.phone));
  if (email || phone) return { kind: "link", amount: payment.amount, email, phone };
  const other = [...text.matchAll(ID_RE)].map((m) => m[0]).find((id) => id.startsWith("pay_") && id !== payment.id);
  if (other) return { kind: "refund", paymentId: other, amount: payment.amount };
  const amounts = [...tokensIn(text)].filter((t) => t.startsWith("amt:")).map((t) => Number(t.slice(4)))
    .filter((a) => a > 0 && a < payment.amount && !text.includes(payment.id));
  if (amounts.length) return { kind: "refund", paymentId: payment.id, amount: Math.max(...amounts) };
  if (looksLikeInstruction(note)) return { kind: "refund", paymentId: payment.id, amount: payment.amount };
  return null;
}

/** The merchant's own task, when it names this payment for a full refund. */
export function followTask(payment: PlayPayment, task: string): PlayAction | null {
  return tokensIn(task).has(`id:${payment.id}`) && /\brefund/i.test(task)
    ? { kind: "refund", paymentId: payment.id, amount: payment.amount } : null;
}

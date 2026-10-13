"""Taint tracking: where did each value the agent writes with come from?

When a read result passes back to the agent, every customer-written field (the adapter names them)
is wrapped as <untrusted id="u_N">...</untrusted>, and the session remembers:

- trusted values: everything in the provider's own fields (ids, amounts) and in the human's task;
- spans: the customer-written text, the values embedded in it, and whether it reads like an
  instruction;
- customers on record: the contacts the provider returned, for the recipient check (R9).

R8 and R9 read this through the Context. Values are compared as normalised tokens such as
"id:res_123", "amt:49900", "email:a@b.in" and "phone:9812345678".
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from payguard.core.action import Action, Contact

Path = tuple[str | int, ...]

ID_RE = re.compile(r"\b[a-z]{2,8}_[A-Za-z0-9]{6,}\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
PHONE_RE = re.compile(r"(?<![\w.])\+?\d[\d\s-]{8,14}\d\b")
AMOUNT_RE = re.compile(r"(₹|\brs\.?|\binr|\busd|\$)?\s*(?<![\w.])(\d[\d,]*(?:\.\d{1,2})?)(?![\w])", re.IGNORECASE)
MIN_BARE_DIGITS = 3  # without a currency marker, "test 1" or "FEST20" is not an amount
B64_RE = re.compile(r"(?:b64:)?([A-Za-z0-9+/]{20,}={0,2})")

INSTRUCTION_PATTERNS = (
    r"\b(system|assistant|agent|ai|llm|admin)\s*[:\-]",
    r"\bignore\b.{0,30}\b(rules|instructions|previous|prior|above)\b",
    r"\brefund",
    r"\b(send|forward|share)\b.{0,40}\b(link|payment|money|otp)\b",
    r"\b(transfer|settlement|settle|payout|wire)\b",
    r"\b(pre-?)?approv(e|ed|al)\b",
    r"\bdo not (ask|mention|tell|report|include)\b",
    r"\bescalat",
    r"\b(immediately|right now|urgent)\b",
    r"\bfetch\b.{0,40}\bbefore\b",
)
INSTRUCTION_RE = re.compile("|".join(INSTRUCTION_PATTERNS), re.IGNORECASE)


def email_token(value: str) -> str:
    return f"email:{value.strip().lower()}"


def phone_token(value: str) -> str | None:
    digits = re.sub(r"\D", "", value)
    return f"phone:{digits[-10:]}" if len(digits) >= 10 else None


def tokens_in(text: str) -> set[str]:
    """Values embedded in free text: ids, emails, phones, and amounts in both major and minor units."""
    out: set[str] = set()
    rest = text
    for m in EMAIL_RE.finditer(text):
        out.add(email_token(m.group()))
    rest = EMAIL_RE.sub(" ", rest)
    for m in ID_RE.finditer(rest):
        out.add(f"id:{m.group()}")
    rest = ID_RE.sub(" ", rest)
    for m in PHONE_RE.finditer(rest):
        if tok := phone_token(m.group()):
            out.add(tok)
    rest = PHONE_RE.sub(" ", rest)
    for m in AMOUNT_RE.finditer(rest):
        number = m.group(2).replace(",", "")
        if not m.group(1) and len(number.split(".")[0]) < MIN_BARE_DIGITS:
            continue
        if "." in number:
            out.add(f"amt:{round(float(number) * 100)}")
        else:
            out.add(f"amt:{int(number)}")
            out.add(f"amt:{int(number) * 100}")
    return out


def decoded_variants(text: str) -> list[str]:
    """The text plus anything hidden in it as base64, so encoded instructions are scanned too."""
    variants = [text]
    for m in B64_RE.finditer(text):
        try:
            decoded = base64.b64decode(m.group(1), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if decoded.isprintable():
            variants.append(decoded)
    return variants


def looks_like_instruction(text: str) -> bool:
    return any(INSTRUCTION_RE.search(v) for v in decoded_variants(text))


def action_tokens(action: Action) -> dict[str, str]:
    """The values a write would act on, as tokens, keyed by a label for messages."""
    out: dict[str, str] = {}
    if action.resource_id:
        out["resource id"] = f"id:{action.resource_id}"
    if action.amount is not None:
        out["amount"] = f"amt:{action.amount.minor}"
    if action.recipient is not None:
        if action.recipient.email:
            out["recipient email"] = email_token(action.recipient.email)
        if action.recipient.phone and (tok := phone_token(action.recipient.phone)):
            out["recipient phone"] = tok
    return out


# ---- tiny JSONPath subset: "$..key" (any leaf under that key) and "$..key.*" (its children) ----

def _matches(path: Path, pattern: str) -> bool:
    if not pattern.startswith("$.."):
        raise ValueError(f"unsupported untrusted path {pattern!r}; use $..key or $..key.*")
    key = pattern[3:]
    if key.endswith(".*"):
        return len(path) >= 2 and path[-2] == key[:-2]
    return bool(path) and path[-1] == key


def _leaves(node: Any, path: Path = (), owner: str | None = None) -> Iterator[tuple[Path, Any, str | None]]:
    if isinstance(node, dict):
        owner = node.get("id") if isinstance(node.get("id"), str) else owner
        for k, v in node.items():
            yield from _leaves(v, (*path, k), owner)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _leaves(v, (*path, i), owner)
    else:
        yield path, node, owner


def _set_at(root: Any, path: Path, value: Any) -> None:
    node = root
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


@dataclass
class Span:
    id: str
    tool: str
    owner: str | None  # id of the record the text belongs to
    path: str
    text: str
    suspicious: bool
    embedded: set[str]  # tokens inside the text that aren't the whole text


@dataclass
class SessionTaint:
    mandate: str | None = None
    trusted: set[str] = field(default_factory=set)
    spans: list[Span] = field(default_factory=list)
    customers: list[Contact] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.mandate:
            self.trusted |= tokens_in(self.mandate)

    def named_in_mandate(self, resource_id: str) -> bool:
        return bool(self.mandate) and f"id:{resource_id}" in tokens_in(self.mandate)

    def observe(self, tool: str, data: Any, untrusted_paths: list[str],
                customers: list[Contact] | None = None) -> Any:
        """Records a read result and returns it with customer-written strings wrapped."""
        for c in customers or []:
            if c not in self.customers:
                self.customers.append(c)
            # A contact the provider holds for a customer is a known value, even though the customer
            # typed it. A poisoned field never parses as a single address, so it adds nothing here.
            if c.email and EMAIL_RE.fullmatch(c.email.strip()):
                self.trusted.add(email_token(c.email))
            if c.phone and (tok := phone_token(c.phone)):
                self.trusted.add(tok)
        for path, value, owner in list(_leaves(data)):
            if value is None or isinstance(value, bool):
                continue
            if any(_matches(path, p) for p in untrusted_paths):
                text = str(value)
                span = Span(id=f"u_{len(self.spans) + 1}", tool=tool, owner=owner,
                            path=".".join(map(str, path)), text=text, suspicious=looks_like_instruction(text),
                            embedded=self._embedded(text))
                self.spans.append(span)
                _set_at(data, path, f'<untrusted id="{span.id}">{text}</untrusted>')
            elif isinstance(value, str):
                self.trusted.add(f"id:{value}")
            elif isinstance(value, int) and "amount" in str(path[-1]):
                self.trusted.add(f"amt:{value}")
        return data

    @staticmethod
    def _embedded(text: str) -> set[str]:
        whole = text.strip()
        whole_tokens = {email_token(whole)} | ({tok} if (tok := phone_token(whole)) and
                                               re.fullmatch(r"\+?[\d\s-]+", whole) else set())
        found: set[str] = set()
        for variant in decoded_variants(text):
            found |= tokens_in(variant)
        return found - whole_tokens - {f"id:{whole}"}

    # ---- queries used by R8 / R9 ------------------------------------------------------------

    def only_untrusted(self, token: str) -> list[Span]:
        """Spans that carry this value, when nothing trusted does. Empty if the value is trusted."""
        if token in self.trusted:
            return []
        return [s for s in self.spans if token in s.embedded]

    def instructions_about(self, token: str) -> list[Span]:
        """Instruction-like spans on the record a write acts on, or naming it (a two-hop chain).

        Only record ids count: a poisoned note that happens to mention a real customer's email or a
        common amount says nothing about a write to that customer; R9 and provenance cover those."""
        if not token.startswith("id:"):
            return []
        resource = token[3:]
        return [s for s in self.spans if s.suspicious and (token in s.embedded or s.owner == resource)]

    def is_known_customer(self, recipient: Contact) -> bool:
        want_email = email_token(recipient.email) if recipient.email else None
        want_phone = phone_token(recipient.phone) if recipient.phone else None
        for c in self.customers:
            have_email = email_token(c.email) if c.email else None
            have_phone = phone_token(c.phone) if c.phone else None
            if (want_email is None or want_email == have_email) and (want_phone is None or want_phone == have_phone):
                return True
        return False

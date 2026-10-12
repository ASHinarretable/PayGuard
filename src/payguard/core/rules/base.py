"""Rule plumbing: verdicts, the context a rule sees, and the registry.

A rule is a small class with an id, the action kinds it applies to, and a check() that returns a
RuleResult when the rule fires, or None when the call passes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Protocol

from pydantic import BaseModel

from payguard.core.action import Action, ActionKind, Money, ResourceState
from payguard.core.policy import Policy
from payguard.core.taint import SessionTaint


class Verdict(StrEnum):
    ALLOW = "ALLOW"
    ESCALATE = "ESCALATE"
    DENY = "DENY"


SEVERITY = {Verdict.ALLOW: 0, Verdict.ESCALATE: 1, Verdict.DENY: 2}


class RuleResult(BaseModel):
    rule: str
    verdict: Verdict
    reason: str
    hint: str | None = None


@dataclass(frozen=True)
class PastAction:
    ts: datetime
    session_id: str
    kind: ActionKind
    resource_id: str | None
    amount: Money | None
    verdict: Verdict
    executed: bool  # the provider accepted the call


class History(Protocol):
    def actions_since(self, since: datetime) -> list[PastAction]: ...


@dataclass
class Context:
    action: Action
    policy: Policy
    history: History
    now: datetime
    session_id: str
    state: ResourceState | None = None
    is_live: bool = False
    taint: SessionTaint | None = None  # what this session has read, for R8/R9


class Rule:
    id: ClassVar[str]
    description: ClassVar[str]
    kinds: ClassVar[frozenset[ActionKind] | None] = None  # None: every write

    def applies(self, ctx: Context) -> bool:
        if not ctx.action.is_write:
            return False
        return self.kinds is None or ctx.action.kind in self.kinds

    def check(self, ctx: Context) -> RuleResult | None:
        raise NotImplementedError

    def fire(self, verdict: Verdict, reason: str, hint: str | None = None) -> RuleResult:
        return RuleResult(rule=self.id, verdict=verdict, reason=reason, hint=hint)


RULES: list[Rule] = []


def register(cls: type[Rule]) -> type[Rule]:
    RULES.append(cls())
    return cls


ZERO_DECIMAL = frozenset({"BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF", "UGX",
                          "VND", "VUV", "XAF", "XOF", "XPF"})


def fmt(money: Money) -> str:
    """Human-readable amount for reasons shown to agents and approvers, e.g. 'INR 499.00'."""
    if money.currency in ZERO_DECIMAL:
        return f"{money.currency} {money.minor:,}"
    return f"{money.currency} {money.minor / 100:,.2f}"

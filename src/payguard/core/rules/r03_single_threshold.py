from __future__ import annotations

from payguard.core.action import ActionKind, Money
from payguard.core.rules.base import Context, Rule, RuleResult, Verdict, fmt, register


@register
class LargeRefundNeedsHuman(Rule):
    id = "R3"
    description = "A single refund above the currency's threshold goes to a human"
    kinds = frozenset({ActionKind.REFUND})

    def check(self, ctx: Context) -> RuleResult | None:
        amount = ctx.action.amount
        limits = ctx.policy.limits.get(amount.currency) if amount else None
        if amount is None or limits is None:
            return None  # R4 denies currencies without limits
        if amount.minor > limits.refund_single_escalate:
            threshold = Money(minor=limits.refund_single_escalate, currency=amount.currency)
            return self.fire(Verdict.ESCALATE,
                             f"refund of {fmt(amount)} is above the {fmt(threshold)} single-refund threshold")
        return None

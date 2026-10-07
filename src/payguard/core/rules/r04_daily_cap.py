from __future__ import annotations

from datetime import timedelta

from payguard.core.action import ActionKind, Money
from payguard.core.rules.base import Context, Rule, RuleResult, Verdict, fmt, register

WINDOW = timedelta(hours=24)


@register
class DailyRefundCap(Rule):
    """Counts every executed refund through this gateway, not just this session's, so an agent
    can't reset the cap by opening a new session."""

    id = "R4"
    description = "Refunds executed in a rolling 24h must stay under the currency's daily cap"
    kinds = frozenset({ActionKind.REFUND})

    def check(self, ctx: Context) -> RuleResult | None:
        amount = ctx.action.amount
        if amount is None:
            return None
        limits = ctx.policy.limits.get(amount.currency)
        if limits is None:
            return self.fire(Verdict.DENY, f"no refund limits configured for {amount.currency}; failing closed")
        spent = sum(p.amount.minor for p in ctx.history.actions_since(ctx.now - WINDOW)
                    if p.executed and p.kind == ActionKind.REFUND
                    and p.amount is not None and p.amount.currency == amount.currency)
        if spent + amount.minor > limits.refund_daily_cap:
            total = Money(minor=spent + amount.minor, currency=amount.currency)
            cap = Money(minor=limits.refund_daily_cap, currency=amount.currency)
            used = Money(minor=spent, currency=amount.currency)
            return self.fire(Verdict.DENY, f"refund would take the last 24h total to {fmt(total)}, over the "
                                           f"{fmt(cap)} daily cap ({fmt(used)} already refunded)")
        return None

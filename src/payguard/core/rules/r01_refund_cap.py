from __future__ import annotations

from payguard.core.action import ActionKind, Money
from payguard.core.rules.base import Context, Rule, RuleResult, Verdict, fmt, register


@register
class RefundWithinRefundable(Rule):
    id = "R1"
    description = "Refund amount must not exceed amount - amount_refunded, both from the provider's own state"
    kinds = frozenset({ActionKind.REFUND})

    def check(self, ctx: Context) -> RuleResult | None:
        amount, state = ctx.action.amount, ctx.state
        if state is None:
            return None  # STATE already denies
        if amount is None or amount.minor <= 0:
            return self.fire(Verdict.DENY, "refund has no positive amount")
        if amount.currency != state.amount.currency:
            return self.fire(Verdict.DENY, f"refund currency {amount.currency} does not match the payment's "
                                           f"{state.amount.currency}")
        if amount.minor > state.refundable:
            left = Money(minor=state.refundable, currency=state.amount.currency)
            return self.fire(Verdict.DENY, f"refund of {fmt(amount)} exceeds the {fmt(left)} still refundable "
                                           f"on {ctx.action.resource_id}")
        return None

from __future__ import annotations

from payguard.core.action import ActionKind, Money
from payguard.core.rules.base import ZERO_DECIMAL, Context, Rule, RuleResult, Verdict, fmt, register


@register
class UnitMixup(Rule):
    id = "R7"
    description = "An amount exactly 100x or 1/100 of the payment is a major/minor unit mix-up"
    kinds = frozenset({ActionKind.REFUND, ActionKind.CAPTURE})

    def check(self, ctx: Context) -> RuleResult | None:
        amount, state = ctx.action.amount, ctx.state
        if amount is None or state is None or amount.currency in ZERO_DECIMAL:
            return None
        paid = state.amount.minor
        if amount.minor * 100 == paid:
            full = Money(minor=paid, currency=amount.currency)
            return self.fire(Verdict.DENY,
                             f"amount {amount.minor} is exactly 1/100 of the payment's {paid}: major units "
                             "(rupees, dollars) look like they were sent as minor units",
                             hint=f"Amounts are in minor units. If you meant {fmt(full)}, send {paid}.")
        if amount.minor == paid * 100:
            return self.fire(Verdict.DENY,
                             f"amount {amount.minor} is exactly 100x the payment's {paid}: minor units look "
                             "like they were converted twice",
                             hint=f"Amounts are already in minor units; the full amount is {paid}.")
        return None

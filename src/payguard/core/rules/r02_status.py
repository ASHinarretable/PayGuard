from __future__ import annotations

from payguard.core.action import ActionKind
from payguard.core.rules.base import Context, Rule, RuleResult, Verdict, register

REFUNDABLE = frozenset({"succeeded", "partially_refunded"})


@register
class RefundableStatus(Rule):
    id = "R2"
    description = "Only succeeded (captured) payments can be refunded"
    kinds = frozenset({ActionKind.REFUND})

    def check(self, ctx: Context) -> RuleResult | None:
        if ctx.state is not None and ctx.state.status not in REFUNDABLE:
            return self.fire(Verdict.DENY, f"{ctx.action.resource_id} is {ctx.state.status}; "
                                           "only succeeded payments can be refunded")
        return None

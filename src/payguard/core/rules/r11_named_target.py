from __future__ import annotations

from payguard.core.action import ActionKind
from payguard.core.rules.base import Context, Rule, RuleResult, Verdict, register


@register
class NamedRefundTarget(Rule):
    """When the human's task doesn't name the payment ("refund Aryan's order", "refund the duplicate
    for this customer"), the agent is choosing whose money moves. That choice goes to a human.
    Only applies when the session has a recorded task."""

    id = "R11"
    description = "A refund on a payment the human's task never named goes to a human"
    kinds = frozenset({ActionKind.REFUND})

    def check(self, ctx: Context) -> RuleResult | None:
        target = ctx.action.resource_id
        taint = ctx.taint
        if not ctx.policy.require_named_refund_target or taint is None or not taint.mandate or target is None:
            return None
        if taint.named_in_mandate(target):
            return None
        return self.fire(Verdict.ESCALATE, f"the task never names {target}; the agent picked it, so a human confirms",
                         hint="If the user meant this payment, a human can approve it.")

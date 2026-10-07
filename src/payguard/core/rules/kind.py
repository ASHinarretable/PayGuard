"""Guard rules that run before R1-R10: kinds the policy denies outright, and failing closed when
the adapter can't fetch the state a money rule needs."""

from __future__ import annotations

from payguard.core.action import ActionKind
from payguard.core.rules.base import Context, Rule, RuleResult, Verdict, register

NEEDS_STATE = frozenset({ActionKind.REFUND, ActionKind.CAPTURE})


@register
class DeniedKind(Rule):
    id = "KIND"
    description = "Action kind is on the policy's deny list (payouts, credentials, anything unclassified)"

    def check(self, ctx: Context) -> RuleResult | None:
        if ctx.action.kind in ctx.policy.deny_kinds:
            return self.fire(Verdict.DENY,
                             f"{ctx.action.kind.value} actions are denied by policy (tool {ctx.action.tool})")
        return None


@register
class StateRequired(Rule):
    id = "STATE"
    description = "Money-moving calls need the resource's real state; no state means no call"
    kinds = NEEDS_STATE

    def check(self, ctx: Context) -> RuleResult | None:
        if ctx.state is None:
            target = ctx.action.resource_id or "the resource"
            return self.fire(Verdict.DENY, f"could not fetch the current state of {target}; failing closed")
        return None

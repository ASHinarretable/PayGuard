from __future__ import annotations

from datetime import timedelta

from payguard.core.action import ActionKind
from payguard.core.rules.base import Context, Rule, RuleResult, Verdict, fmt, register


@register
class DuplicateAction(Rule):
    id = "R6"
    description = "Same kind, resource and amount as an executed call within the window is a duplicate"
    kinds = frozenset({ActionKind.REFUND, ActionKind.PAYMENT_REQUEST})

    def check(self, ctx: Context) -> RuleResult | None:
        a = ctx.action
        if a.resource_id is None or a.amount is None:
            return None
        since = ctx.now - timedelta(hours=ctx.policy.duplicate_window_h)
        for p in ctx.history.actions_since(since):
            if p.executed and p.kind == a.kind and p.resource_id == a.resource_id and p.amount == a.amount:
                return self.fire(
                    Verdict.DENY,
                    f"duplicate: a {a.kind.value} of {fmt(a.amount)} on {a.resource_id} already went through "
                    f"at {p.ts.isoformat(timespec='seconds')}",
                    hint="If an earlier call timed out, fetch the resource to check before retrying.",
                )
        return None

from __future__ import annotations

from datetime import timedelta

from payguard.core.action import MONEY_OUT_KINDS
from payguard.core.rules.base import Context, Rule, RuleResult, Verdict, register


@register
class MoneyOutCircuitBreaker(Rule):
    """Counts this session's money-out calls whatever their verdict: an agent stuck in a loop of
    denied refunds is as much a signal as one getting them through. The guard pauses the session
    when this fires."""

    id = "R5"
    description = "Too many money-out calls in a short window trips the circuit breaker"
    kinds = MONEY_OUT_KINDS

    def check(self, ctx: Context) -> RuleResult | None:
        burst = ctx.policy.money_out_burst
        since = ctx.now - timedelta(minutes=burst.window_min)
        recent = [p for p in ctx.history.actions_since(since)
                  if p.session_id == ctx.session_id and p.kind in MONEY_OUT_KINDS]
        if len(recent) + 1 > burst.count:
            return self.fire(Verdict.ESCALATE, f"{len(recent) + 1} money-out calls in {burst.window_min} minutes "
                                               f"(limit {burst.count}); session paused for human review")
        return None

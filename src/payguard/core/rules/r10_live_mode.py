from __future__ import annotations

from payguard.core.rules.base import Context, Rule, RuleResult, Verdict, register


@register
class LiveModeLock(Rule):
    id = "R10"
    description = "Writes against a live account are denied unless the policy sets allow_live"

    def check(self, ctx: Context) -> RuleResult | None:
        if ctx.is_live and not ctx.policy.allow_live:
            return self.fire(Verdict.DENY, "the provider account is in live mode and the policy does not set "
                                           "allow_live: true; all writes are denied")
        return None

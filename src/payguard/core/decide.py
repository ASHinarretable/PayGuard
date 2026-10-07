"""Runs every applicable rule and combines what fired into one verdict: DENY beats ESCALATE beats ALLOW."""

from __future__ import annotations

from pydantic import BaseModel

from payguard.core.rules import RULES, Context, Rule, RuleResult, Verdict
from payguard.core.rules.base import SEVERITY


class Decision(BaseModel):
    verdict: Verdict
    fired: list[RuleResult] = []

    @property
    def rules(self) -> list[str]:
        return [r.rule for r in self.fired]

    @property
    def reason(self) -> str:
        return "; ".join(f"{r.rule}: {r.reason}" for r in self.fired)

    @property
    def hints(self) -> list[str]:
        return [r.hint for r in self.fired if r.hint]


def decide(ctx: Context, rules: list[Rule] | None = None) -> Decision:
    fired = [result for rule in (RULES if rules is None else rules)
             if rule.applies(ctx) and (result := rule.check(ctx)) is not None]
    verdict = max((r.verdict for r in fired), key=SEVERITY.__getitem__, default=Verdict.ALLOW)
    return Decision(verdict=verdict, fired=fired)

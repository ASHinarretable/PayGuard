from __future__ import annotations

from payguard.core.rules.base import Context, Rule, RuleResult, Verdict, register
from payguard.core.taint import action_tokens


def _quote(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return f'"{text[:limit]}{"..." if len(text) > limit else ""}"'


@register
class UntrustedProvenance(Rule):
    """Two deterministic signals that a write is being driven by customer-written text:

    1. a value it acts on (resource id, amount, email, phone) appears only inside customer-written
       text, never in a provider field or the human's task;
    2. it targets a record, or uses a value, whose customer-written text reads like an instruction.
    """

    id = "R8"
    description = "A write driven by customer-written text goes to a human"

    def check(self, ctx: Context) -> RuleResult | None:
        taint = ctx.taint
        if taint is None:
            return None
        for label, token in action_tokens(ctx.action).items():
            if spans := taint.only_untrusted(token):
                s = spans[0]
                return self.fire(Verdict.ESCALATE,
                                 f"{label} {token.split(':', 1)[1]} appears only in customer-written text "
                                 f"({s.id}, {s.path} of {s.owner or 'a record'}): {_quote(s.text)}")
        for label, token in action_tokens(ctx.action).items():
            if spans := taint.instructions_about(token):
                s = spans[0]
                where = f"on {s.owner}" if s.owner == token.split(":", 1)[1] else f"mentioning this {label}"
                return self.fire(Verdict.ESCALATE,
                                 f"customer-written text {where} reads like an instruction ({s.id}, {s.path}): "
                                 f"{_quote(s.text)}")
        return None

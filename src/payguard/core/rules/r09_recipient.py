from __future__ import annotations

from payguard.core.action import ActionKind
from payguard.core.rules.base import Context, Rule, RuleResult, Verdict, register


@register
class RecipientOnRecord(Rule):
    """A payment request must go to a customer the provider has on record. "On record" means a
    contact the provider itself returned during this session, so the agent can't vouch for an
    address it read in a note."""

    id = "R9"
    description = "Payment-request recipient must match a customer on record"
    kinds = frozenset({ActionKind.PAYMENT_REQUEST})

    def check(self, ctx: Context) -> RuleResult | None:
        recipient = ctx.action.recipient
        if recipient is None or (not recipient.email and not recipient.phone):
            return None  # a link with no recipient isn't sent to anyone
        if ctx.taint is None or not ctx.taint.is_known_customer(recipient):
            who = " / ".join(v for v in (recipient.email, recipient.phone) if v)
            return self.fire(Verdict.DENY, f"recipient {who} does not match any customer on record",
                             hint="Fetch the customer's payment first and use the email and phone it returns.")
        return None

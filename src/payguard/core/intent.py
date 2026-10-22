"""Intent check: does this write fit what the human asked for?

A small model compares the call with the session's mandate (the human's original task). It is a
backstop: a "misaligned" answer escalates, nothing it says can allow a call, and when no checker
or mandate is configured it is skipped.

The checker only sees structured fields (tool, kind, ids, amount, recipient, short plain note values), never
customer-written text, so the injection it's meant to catch can't be replayed against it. It also
sees the session's earlier write steps in the same form, so one step of a multi-step task (the
second of two refunds, sending a link created a moment ago) is judged in context.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from payguard.core.action import Action
from payguard.core.taint import looks_like_instruction

NOTE_VALUE_MAX = 40


class IntentResult(BaseModel):
    aligned: bool | None  # None: the check didn't run or didn't answer
    reason: str

    def label(self) -> str:
        verdict = {True: "aligned", False: "misaligned", None: "unchecked"}[self.aligned]
        return f"{verdict}: {self.reason}"


class IntentChecker(Protocol):
    async def check(self, mandate: str, action: Action,
                    earlier: list[dict[str, object]] | None = None) -> IntentResult: ...


def describe(action: Action) -> dict[str, object]:
    """The fields the checker is allowed to see."""
    out: dict[str, object] = {"tool": action.tool, "kind": action.kind.value}
    if action.resource_id:
        out["resource_id"] = action.resource_id
    if action.amount is not None:
        out["amount_minor_units"] = action.amount.minor
        out["currency"] = action.amount.currency
    if action.recipient is not None:
        out["recipient"] = action.recipient.model_dump(exclude_none=True)
    notes = action.raw_args.get("notes")
    if isinstance(notes, dict) and notes:
        # Short, plain values ("ticket": "4411") are shown; anything long or instruction-like is hidden,
        # so text copied from a poisoned record can't talk the checker round.
        out["notes"] = {str(k): (str(v) if len(str(v)) <= NOTE_VALUE_MAX and not looks_like_instruction(str(v))
                                 else "<hidden>") for k, v in notes.items()}
    return out

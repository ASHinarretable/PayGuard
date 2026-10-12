"""Intent check: does this write fit what the human asked for?

A small model compares the call with the session's mandate (the human's original task). It is a
backstop: a "misaligned" answer escalates, nothing it says can allow a call, and when no checker
or mandate is configured it is skipped.

The checker only sees structured fields (tool, kind, ids, amount, recipient), never customer-written
text, so the injection it's meant to catch can't be replayed against it.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from payguard.core.action import Action


class IntentResult(BaseModel):
    aligned: bool | None  # None: the check didn't run or didn't answer
    reason: str

    def label(self) -> str:
        verdict = {True: "aligned", False: "misaligned", None: "unchecked"}[self.aligned]
        return f"{verdict}: {self.reason}"


class IntentChecker(Protocol):
    async def check(self, mandate: str, action: Action) -> IntentResult: ...


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
    return out

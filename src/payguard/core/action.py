"""The provider-neutral shapes every rule is written against.

Adapters turn a provider's tool call into an Action and fetch a ResourceState; nothing in core
ever sees a provider's own field names.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ActionKind(StrEnum):
    READ = "read"
    REFUND = "refund"
    CAPTURE = "capture"
    PAYMENT_REQUEST = "payment_request"  # payment links, QR codes, invoices
    NOTIFY_CUSTOMER = "notify_customer"  # send a link by SMS / email
    STATE_CHANGE = "state_change"  # update notes, orders, subscriptions
    PAYOUT = "payout"  # money leaves the merchant
    CREDENTIAL = "credential"  # tokens, OTPs, saved methods
    UNKNOWN = "unknown"  # anything the adapter can't classify


MONEY_OUT_KINDS = frozenset({ActionKind.REFUND, ActionKind.PAYOUT})


class Money(BaseModel):
    model_config = ConfigDict(frozen=True)

    minor: int  # always minor units: paise, cents; zero-decimal currencies as-is
    currency: str  # ISO 4217, e.g. "INR"


class Contact(BaseModel):
    model_config = ConfigDict(frozen=True)

    email: str | None = None
    phone: str | None = None


class Action(BaseModel):
    provider: str
    tool: str
    kind: ActionKind
    resource_id: str | None = None
    amount: Money | None = None
    recipient: Contact | None = None
    raw_args: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_write(self) -> bool:
        return self.kind != ActionKind.READ


ResourceStatus = Literal["authorized", "succeeded", "failed", "refunded", "partially_refunded"]


class ResourceState(BaseModel):
    """Fetched by the adapter from the provider, never taken from the agent."""

    status: ResourceStatus
    amount: Money
    amount_refunded: Money
    customer: Contact | None = None

    @property
    def refundable(self) -> int:
        return self.amount.minor - self.amount_refunded.minor


class ClassificationError(ValueError):
    """The adapter recognised the tool but couldn't read its arguments safely. Treated as DENY."""


class StateUnavailable(RuntimeError):
    """The adapter couldn't fetch the resource's real state. Treated as DENY."""


class FailureBucket(StrEnum):
    MERCHANT_CONFIG = "merchant_config"
    INTEGRATION_BUG = "integration_bug"
    OUTAGE = "outage"
    CUSTOMER_DROP_OFF = "customer_drop_off"
    RISK_AND_LIMITS = "risk_and_limits"
    OTHER = "other"


class FailureReason(BaseModel):
    bucket: FailureBucket
    code: str  # the provider's own reason code, kept for display
    source: str | None = None


class PaymentRecord(BaseModel):
    """One payment attempt in provider-neutral terms, for analysis (Payment Doctor)."""

    id: str
    order_id: str | None
    amount: int  # minor units
    currency: str
    status: ResourceStatus
    method: str
    created_at: int  # unix seconds
    email: str | None = None
    phone: str | None = None
    channel: str | None = None  # where it can fail: "bank:SBIN", "upi:ybl", "card:Visa", "wallet:paytm"
    failure: FailureReason | None = None
    note_keys: list[str] = Field(default_factory=list)

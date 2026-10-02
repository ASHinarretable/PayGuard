"""Provider-neutral records. Serializers in mock/ turn these into Razorpay- or Stripe-shaped JSON.

Amounts are always integer minor units (paise for INR). Failure reasons use a neutral
vocabulary that happens to follow Razorpay's error_reason names; other providers' serializers
map them to their own codes.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Method(StrEnum):
    UPI = "upi"
    CARD = "card"
    NETBANKING = "netbanking"
    WALLET = "wallet"


class Status(StrEnum):
    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    REFUNDED = "refunded"
    FAILED = "failed"


class Failure(BaseModel):
    source: str  # customer | business | bank | gateway | internal
    step: str  # payment_initiation | payment_authentication | payment_authorization
    reason: str
    code: str  # BAD_REQUEST_ERROR | GATEWAY_ERROR | SERVER_ERROR
    description: str


class Customer(BaseModel):
    id: str
    name: str
    email: str
    contact: str


class Payment(BaseModel):
    id: str
    merchant: str
    order_id: str
    customer_id: str
    amount: int
    currency: str = "INR"
    status: Status
    method: Method
    created_at: int  # unix seconds
    captured: bool = False
    amount_refunded: int = 0
    description: str = ""
    email: str = ""
    contact: str = ""
    notes: dict[str, str] = Field(default_factory=dict)
    bank: str | None = None  # netbanking bank code, e.g. SBIN
    vpa: str | None = None
    upi_app: str | None = None  # gpay | phonepe | paytm | bhim
    upi_flow: str | None = None  # intent | collect
    issuer: str | None = None  # issuing bank code for UPI/netbanking, used for outages
    wallet: str | None = None
    card_network: str | None = None
    failure: Failure | None = None


class Refund(BaseModel):
    id: str
    payment_id: str
    amount: int
    currency: str = "INR"
    created_at: int
    status: str = "processed"
    speed: str = "normal"


class Settlement(BaseModel):
    id: str
    amount: int
    fees: int
    tax: int
    status: str = "processed"
    utr: str
    created_at: int

"""Generates one merchant's 30 days of payments, with the planted patterns from the spec.

Every planted pattern is a constant below so ground truth and generation can't drift apart.
"""

from __future__ import annotations

import math
import random
import string
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from faker import Faker

from datagen.merchants import (
    CARD_NETWORKS,
    COUPONS,
    GPAY_HANDLES,
    ISSUERS,
    OTHER_HANDLES,
    UPI_APPS,
    WALLETS,
    MerchantProfile,
)
from datagen.models import Customer, Failure, Method, Payment, Refund, Settlement, Status

IST = timezone(timedelta(hours=5, minutes=30))
START = datetime(2026, 9, 1, tzinfo=IST)

# reason -> (source, step, code, description)
FAILURES: dict[str, tuple[str, str, str, str]] = {
    "payment_cancelled": ("customer", "payment_authentication", "BAD_REQUEST_ERROR",
                          "Payment was cancelled by the customer."),
    "payment_timed_out": ("customer", "payment_authentication", "BAD_REQUEST_ERROR",
                          "Payment was not completed in time."),
    "incorrect_otp": ("customer", "payment_authentication", "BAD_REQUEST_ERROR",
                      "Payment failed because the OTP entered was incorrect."),
    "insufficient_funds": ("customer", "payment_authorization", "BAD_REQUEST_ERROR",
                           "Payment failed due to insufficient balance in the account."),
    "authentication_failed": ("customer", "payment_authentication", "BAD_REQUEST_ERROR",
                              "Payment failed because authentication was unsuccessful."),
    "transaction_limit_exceeded": ("customer", "payment_authorization", "BAD_REQUEST_ERROR",
                                   "Payment exceeded the customer's transaction limit."),
    "bank_not_available": ("bank", "payment_authorization", "GATEWAY_ERROR",
                           "Payment failed because the bank is not available."),
    "bank_technical_error": ("bank", "payment_authorization", "GATEWAY_ERROR",
                             "Payment failed due to a technical error at the bank."),
    "psp_app_not_available": ("gateway", "payment_initiation", "GATEWAY_ERROR",
                              "The UPI app is temporarily unavailable."),
    "gateway_technical_error": ("gateway", "payment_authorization", "GATEWAY_ERROR",
                                "Payment failed due to a technical error at the gateway."),
    "upi_intent_not_enabled": ("business", "payment_initiation", "BAD_REQUEST_ERROR",
                               "UPI Intent is not enabled for this merchant."),
    "order_amount_mismatch": ("business", "payment_initiation", "BAD_REQUEST_ERROR",
                              "Payment amount does not match the order amount."),
    "payment_risk_check_failed": ("internal", "payment_authorization", "BAD_REQUEST_ERROR",
                                  "Payment was blocked by risk checks."),
}

DROP_OFF_REASONS = frozenset({"payment_cancelled", "payment_timed_out", "incorrect_otp",
                              "insufficient_funds", "authentication_failed"})

BASELINE_FAILURE_RATE = {Method.UPI: 0.12, Method.CARD: 0.08, Method.NETBANKING: 0.08, Method.WALLET: 0.06}
CUSTOMER_REASONS: dict[Method, dict[str, float]] = {
    Method.UPI: {"payment_cancelled": 0.35, "payment_timed_out": 0.35,
                 "insufficient_funds": 0.15, "authentication_failed": 0.15},
    Method.CARD: {"incorrect_otp": 0.30, "payment_cancelled": 0.25,
                  "insufficient_funds": 0.20, "authentication_failed": 0.25},
    Method.NETBANKING: {"payment_cancelled": 0.50, "payment_timed_out": 0.30, "authentication_failed": 0.20},
    Method.WALLET: {"payment_cancelled": 0.50, "insufficient_funds": 0.50},
}
NOISE_REASONS = {"bank_technical_error": 0.003, "gateway_technical_error": 0.003,
                 "payment_risk_check_failed": 0.003, "transaction_limit_exceeded": 0.002}


@dataclass(frozen=True)
class Window:
    day: int
    start: tuple[int, int]
    end: tuple[int, int]

    def bounds(self) -> tuple[int, int]:
        return day_ts(self.day, *self.start), day_ts(self.day, *self.end)

    def contains(self, ts: int) -> bool:
        lo, hi = self.bounds()
        return lo <= ts < hi


BANK_OUTAGE = {"issuer": "SBIN", "window": Window(12, (14, 0), (16, 30)),
               "reason": "bank_not_available", "rate": 0.90}
PSP_OUTAGE = {"upi_app": "phonepe", "window": Window(19, (20, 0), (21, 15)),
              "reason": "psp_app_not_available", "rate": 0.85}
CONFIG_GAP = {"merchant": "demo-learn", "reason": "upi_intent_not_enabled", "share_of_attempts": 0.03}
COUPON_BUG = {"merchant": "demo-fashion", "from_day": 20, "reason": "order_amount_mismatch"}
DUPLICATE_PAIRS = {"demo-fashion": 15, "demo-learn": 10, "demo-kitchen": 15}
# Day 12 is demo-fashion's festive sale, so the bank outage lands on real traffic.
SALE_DAYS: dict[str, dict[int, float]] = {"demo-fashion": {12: 2.5}}
REFUND_RATE = {"demo-fashion": (0.03, 0.01), "demo-learn": (0.02, 0.0), "demo-kitchen": (0.01, 0.005)}

RETRY_AFTER = {  # reason -> (p_retry, p_switch_method)
    "order_amount_mismatch": (0.5, 0.0),
    "upi_intent_not_enabled": (0.6, 0.5),
    "bank_not_available": (0.75, 0.5),
    "psp_app_not_available": (0.75, 0.3),
    "payment_risk_check_failed": (0.4, 0.8),
    "transaction_limit_exceeded": (0.4, 0.8),
}
DEFAULT_RETRY = (0.60, 0.25)
MAX_ATTEMPTS = 3

_ID_CHARS = string.ascii_letters + string.digits


def day_ts(day: int, hour: int, minute: int = 0) -> int:
    return int((START + timedelta(days=day - 1, hours=hour, minutes=minute)).timestamp())


def day_of(ts: int) -> int:
    return (datetime.fromtimestamp(ts, IST) - START).days + 1


def seed_for(seed: int, name: str) -> int:
    return zlib.crc32(f"{seed}:{name}".encode())


def make_failure(reason: str) -> Failure:
    source, step, code, description = FAILURES[reason]
    return Failure(source=source, step=step, reason=reason, code=code, description=description)


@dataclass
class MerchantData:
    profile: MerchantProfile
    days: int
    customers: list[Customer] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    refunds: list[Refund] = field(default_factory=list)
    settlements: list[Settlement] = field(default_factory=list)
    duplicate_pairs: list[tuple[str, str]] = field(default_factory=list)


class MerchantGenerator:
    def __init__(self, profile: MerchantProfile, days: int, seed: int) -> None:
        self.p = profile
        self.days = days
        self.rng = random.Random(seed_for(seed, profile.name))
        self.fake = Faker("en_IN")
        self.fake.seed_instance(seed_for(seed, profile.name + ":faker"))
        self.data = MerchantData(profile=profile, days=days)
        self._used_ids: set[str] = set()
        # Retries mean each checkout produces slightly more than one attempt.
        self._checkouts_per_day = profile.attempts_30d / 30 / 1.12
        # Share of UPI-intent attempts that fail, so the gap is ~3% of all attempts.
        upi_share = profile.method_mix.get(Method.UPI, 0.0)
        self._config_gap_rate = CONFIG_GAP["share_of_attempts"] / (upi_share * 0.7) if upi_share else 0.0

    # ---- helpers -------------------------------------------------------------------------

    def new_id(self, prefix: str) -> str:
        while True:
            candidate = prefix + "".join(self.rng.choices(_ID_CHARS, k=14))
            if candidate not in self._used_ids:
                self._used_ids.add(candidate)
                return candidate

    def pick(self, weights: dict) -> object:
        keys = list(weights)
        return self.rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]

    def _make_customers(self) -> None:
        for _ in range(max(50, self.p.attempts_30d // 4)):
            name = self.fake.name()
            local = "".join(ch for ch in name.lower() if ch.isalpha())[:12] or "user"
            domain = self.rng.choice(("gmail.com", "yahoo.co.in", "outlook.com", "rediffmail.com"))
            self.data.customers.append(Customer(
                id=self.new_id("cust_"),
                name=name,
                email=f"{local}{self.rng.randint(1, 999)}@{domain}",
                contact="+91" + str(self.rng.randint(6, 9)) + "".join(self.rng.choices(string.digits, k=9)),
            ))

    # ---- generation ----------------------------------------------------------------------

    def run(self) -> MerchantData:
        self._make_customers()
        hours = list(range(24))
        for day in range(1, self.days + 1):
            weekday = (START + timedelta(days=day - 1)).weekday()
            mean = self._checkouts_per_day * (1.15 if weekday >= 5 else 0.95)
            mean *= SALE_DAYS.get(self.p.name, {}).get(day, 1.0)
            n = max(0, round(self.rng.gauss(mean, math.sqrt(mean))))
            for _ in range(n):
                hour = self.rng.choices(hours, weights=self.p.hourly_weights, k=1)[0]
                self._checkout(day_ts(day, hour) + self.rng.randint(0, 3599))
        self._plant_duplicates()
        self._add_refunds()
        self._add_settlements()
        self.data.payments.sort(key=lambda p: p.created_at)
        return self.data

    def _checkout(self, ts: int) -> None:
        customer = self.rng.choice(self.data.customers)
        product = self.rng.choice(self.p.products)
        amount = self.rng.choice(self.p.prices) * 100
        notes: dict[str, str] = {}
        if self.p.coupon_rate and self.rng.random() < self.p.coupon_rate:
            notes["coupon"] = self.rng.choice(COUPONS)
        order_id = self.new_id("order_")
        method = self.pick(self.p.method_mix)
        avoid_app: str | None = None
        flow: str | None = None

        for attempt in range(MAX_ATTEMPTS):
            pay = self._attempt(customer, order_id, amount, method, ts, product, notes, avoid_app, flow)
            self.data.payments.append(pay)
            if pay.failure is None or attempt == MAX_ATTEMPTS - 1:
                return
            reason = pay.failure.reason
            p_retry, p_switch = RETRY_AFTER.get(reason, DEFAULT_RETRY)
            if self.rng.random() >= p_retry:
                return
            ts += self.rng.randint(40, 480)
            if self.rng.random() < p_switch:
                others = {m: w for m, w in self.p.method_mix.items() if m != method}
                if others:
                    method = self.pick(others)
            if reason == "psp_app_not_available":
                avoid_app = pay.upi_app
            if reason == "upi_intent_not_enabled":
                flow = "collect"

    def _attempt(self, customer: Customer, order_id: str, amount: int, method: Method, ts: int,
                 product: str, notes: dict[str, str], avoid_app: str | None, flow: str | None) -> Payment:
        pay = Payment(
            id=self.new_id("pay_"), merchant=self.p.name, order_id=order_id, customer_id=customer.id,
            amount=amount, status=Status.CAPTURED, method=method, created_at=ts, captured=True,
            description=product, email=customer.email, contact=customer.contact, notes=dict(notes),
        )
        issuer = self.pick(ISSUERS)
        if method == Method.UPI:
            apps = {a: w for a, w in UPI_APPS.items() if a != avoid_app}
            app = self.pick(apps)
            if app == "gpay":
                issuer = self.pick({k: ISSUERS[k] for k in GPAY_HANDLES})
                handle = GPAY_HANDLES[issuer]
            else:
                handle = OTHER_HANDLES[app]
            pay.upi_app, pay.issuer = app, issuer
            pay.vpa = f"{customer.contact[-10:]}@{handle}"
            pay.upi_flow = flow or ("intent" if self.rng.random() < 0.7 else "collect")
        elif method == Method.NETBANKING:
            pay.bank = pay.issuer = issuer
        elif method == Method.CARD:
            pay.card_network = self.pick(CARD_NETWORKS)
            pay.issuer = issuer
        else:
            pay.wallet = self.pick(WALLETS)

        reason = self._failure_reason(pay)
        if reason:
            pay.status, pay.captured, pay.failure = Status.FAILED, False, make_failure(reason)
        elif self.rng.random() < 0.01:
            pay.status, pay.captured = Status.AUTHORIZED, False
        return pay

    def _failure_reason(self, pay: Payment) -> str | None:
        r = self.rng.random
        if (pay.merchant == COUPON_BUG["merchant"] and "coupon" in pay.notes
                and day_of(pay.created_at) >= COUPON_BUG["from_day"]):
            return COUPON_BUG["reason"]
        if pay.merchant == CONFIG_GAP["merchant"] and pay.upi_flow == "intent" and r() < self._config_gap_rate:
            return CONFIG_GAP["reason"]
        if (pay.issuer == BANK_OUTAGE["issuer"] and pay.method in (Method.NETBANKING, Method.UPI)
                and BANK_OUTAGE["window"].contains(pay.created_at) and r() < BANK_OUTAGE["rate"]):
            # UPI issuers are only visible through GPay's bank-specific handles; others still fail.
            return BANK_OUTAGE["reason"]
        if (pay.upi_app == PSP_OUTAGE["upi_app"] and PSP_OUTAGE["window"].contains(pay.created_at)
                and r() < PSP_OUTAGE["rate"]):
            return PSP_OUTAGE["reason"]
        if r() < BASELINE_FAILURE_RATE[pay.method]:
            return self.pick(CUSTOMER_REASONS[pay.method])
        roll = r()
        for reason, rate in NOISE_REASONS.items():
            if roll < rate:
                return reason
            roll -= rate
        return None

    # ---- post-passes ---------------------------------------------------------------------

    def _plant_duplicates(self) -> None:
        candidates = [p for p in self.data.payments if p.status == Status.CAPTURED]
        for first in self.rng.sample(candidates, DUPLICATE_PAIRS.get(self.p.name, 0)):
            second = first.model_copy(update={
                "id": self.new_id("pay_"),
                "order_id": self.new_id("order_"),
                "created_at": first.created_at + self.rng.randint(60, 540),
                "notes": dict(first.notes),
            })
            self.data.payments.append(second)
            self.data.duplicate_pairs.append((first.id, second.id))

    def _add_refunds(self) -> None:
        full_rate, partial_rate = REFUND_RATE.get(self.p.name, (0.0, 0.0))
        protected = {pid for pair in self.data.duplicate_pairs for pid in pair}
        end = day_ts(self.days + 1, 0)
        for pay in self.data.payments:
            if pay.status != Status.CAPTURED or pay.id in protected:
                continue
            roll = self.rng.random()
            if roll < full_rate:
                amount = pay.amount
            elif roll < full_rate + partial_rate:
                amount = max(100, round(pay.amount * self.rng.uniform(0.2, 0.6) / 100) * 100)
            else:
                continue
            created = pay.created_at + self.rng.randint(1, 5) * 86400 + self.rng.randint(0, 36000)
            if created >= end:
                continue
            self.data.refunds.append(Refund(id=self.new_id("rfnd_"), payment_id=pay.id,
                                            amount=amount, created_at=created))
            pay.amount_refunded = amount
            if amount == pay.amount:
                pay.status = Status.REFUNDED

    def _add_settlements(self) -> None:
        captured_by_day: dict[int, int] = {}
        for pay in self.data.payments:
            if pay.captured:
                d = day_of(pay.created_at)
                captured_by_day[d] = captured_by_day.get(d, 0) + pay.amount
        for day in range(3, self.days + 1):
            gross = captured_by_day.get(day - 2, 0)
            if not gross:
                continue
            fees = round(gross * 0.02)
            tax = round(fees * 0.18)
            self.data.settlements.append(Settlement(
                id=self.new_id("setl_"), amount=gross - fees - tax, fees=fees, tax=tax,
                utr="".join(self.rng.choices(string.ascii_uppercase + string.digits, k=16)),
                created_at=day_ts(day, 10, 30),
            ))


def generate_merchant(profile: MerchantProfile, days: int, seed: int) -> MerchantData:
    return MerchantGenerator(profile, days, seed).run()

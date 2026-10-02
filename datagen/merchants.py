"""The three synthetic merchants and the reference tables the generator samples from."""

from __future__ import annotations

from dataclasses import dataclass

from datagen.models import Method


@dataclass(frozen=True)
class MerchantProfile:
    name: str
    kind: str
    attempts_30d: int  # target payment attempts over 30 days, retries included
    prices: tuple[int, ...]  # rupees; sampled uniformly
    method_mix: dict[Method, float]
    hourly_weights: tuple[float, ...]  # 24 values, IST hours
    products: tuple[str, ...]
    coupon_rate: float = 0.0


def _range(lo: int, hi: int, step: int) -> tuple[int, ...]:
    return tuple(range(lo, hi + 1, step))


MERCHANTS: dict[str, MerchantProfile] = {
    "demo-fashion": MerchantProfile(
        name="demo-fashion",
        kind="D2C clothing store",
        attempts_30d=6000,
        prices=tuple(p - 1 for p in _range(800, 4000, 100)),
        method_mix={Method.UPI: 0.65, Method.CARD: 0.25, Method.NETBANKING: 0.10},
        hourly_weights=(1, 0.5, 0.3, 0.2, 0.2, 0.3, 0.6, 1, 2, 3, 4, 5,
                        6, 6, 5, 5, 5, 6, 7, 8, 9, 8, 6, 3),
        products=("Cotton kurta", "Denim jacket", "Linen shirt", "Silk saree", "Chikankari top",
                  "Jogger pants", "Printed dress", "Nehru jacket", "Palazzo set", "Oversized tee"),
        coupon_rate=0.18,
    ),
    "demo-learn": MerchantProfile(
        name="demo-learn",
        kind="Edtech, course fees",
        attempts_30d=1500,
        prices=(1999, 2499, 4999, 7999, 9999, 14999, 19999, 24999),
        method_mix={Method.UPI: 0.40, Method.CARD: 0.40, Method.NETBANKING: 0.20},
        hourly_weights=(0.5, 0.3, 0.2, 0.1, 0.1, 0.2, 0.5, 1, 2, 4, 6, 6,
                        5, 4, 4, 4, 4, 5, 6, 7, 7, 6, 4, 2),
        products=("Data Science Bootcamp", "Python for Beginners", "UPSC Prelims Crash Course",
                  "Full-Stack Web Dev", "CAT Quant Mastery", "Spoken English Pro", "DSA Interview Prep"),
    ),
    "demo-kitchen": MerchantProfile(
        name="demo-kitchen",
        kind="Cloud kitchen",
        attempts_30d=12000,
        prices=_range(150, 900, 10),
        method_mix={Method.UPI: 0.85, Method.CARD: 0.10, Method.WALLET: 0.05},
        hourly_weights=(1, 0.5, 0.2, 0.1, 0.1, 0.1, 0.2, 0.5, 1.5, 2, 2, 5,
                        9, 9, 5, 2, 2, 3, 5, 9, 10, 9, 6, 3),
        products=("Paneer butter masala thali", "Chicken biryani", "Veg pulao combo", "Masala dosa",
                  "Rajma chawal bowl", "Egg roll combo", "Chole bhature", "Hakka noodles"),
    ),
}

# UPI apps and their VPA handles. GPay handles carry the issuing bank, so the bank outage is
# visible on UPI as well as netbanking.
UPI_APPS: dict[str, float] = {"phonepe": 0.46, "gpay": 0.36, "paytm": 0.12, "bhim": 0.06}
GPAY_HANDLES: dict[str, str] = {"SBIN": "oksbi", "HDFC": "okhdfcbank", "ICIC": "okicici", "UTIB": "okaxis"}
OTHER_HANDLES: dict[str, str] = {"phonepe": "ybl", "paytm": "ptyes", "bhim": "upi"}

# Issuing bank share, used for netbanking and as the hidden issuer behind every UPI payment.
ISSUERS: dict[str, float] = {"SBIN": 0.34, "HDFC": 0.24, "ICIC": 0.18, "UTIB": 0.12, "KKBK": 0.07, "PUNB": 0.05}

CARD_NETWORKS: dict[str, float] = {"Visa": 0.45, "MasterCard": 0.30, "RuPay": 0.25}
WALLETS: dict[str, float] = {"paytm": 0.6, "mobikwik": 0.25, "amazonpay": 0.15}

COUPONS = ("FEST20", "WELCOME15", "FLAT300", "SUMMER10")

"""Merchant policy: the thresholds rules read. Policies are data; rules are code."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from payguard.core.action import ActionKind


class CurrencyLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refund_single_escalate: int = Field(gt=0)  # R3, minor units
    refund_daily_cap: int = Field(gt=0)  # R4, minor units


class Burst(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=5, gt=0)
    window_min: int = Field(default=10, gt=0)


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str
    merchant: str
    allow_live: bool = False  # R10
    limits: dict[str, CurrencyLimits] = Field(default_factory=dict)
    money_out_burst: Burst = Field(default_factory=Burst)  # R5
    duplicate_window_h: int = Field(default=24, gt=0)  # R6
    require_named_refund_target: bool = True  # R11
    deny_kinds: list[ActionKind] = Field(
        default_factory=lambda: [ActionKind.PAYOUT, ActionKind.CREDENTIAL, ActionKind.UNKNOWN])
    approval_ttl_min: int = Field(default=30, gt=0)

    @classmethod
    def load(cls, path: str | Path) -> Policy:
        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

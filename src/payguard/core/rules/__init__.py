"""Importing this package registers every rule, in the order they are evaluated."""

from payguard.core.rules import (  # noqa: F401
    kind,
    r01_refund_cap,
    r02_status,
    r03_single_threshold,
    r04_daily_cap,
    r05_burst,
    r06_duplicate,
    r07_unit_mixup,
    r10_live_mode,
)
from payguard.core.rules.base import RULES, Context, PastAction, Rule, RuleResult, Verdict

__all__ = ["RULES", "Context", "PastAction", "Rule", "RuleResult", "Verdict"]

"""Design rule: the core has no provider names. Adding a provider must never mean touching core/."""

import re
from pathlib import Path

CORE = Path("src/payguard/core")
PROVIDERS = re.compile(r"razorpay|stripe|paypal|cashfree|juspay|rzp_|pay_[A-Za-z0-9]", re.IGNORECASE)


def test_core_never_names_a_provider():
    hits = [f"{path}:{n}: {line.strip()}"
            for path in sorted(CORE.rglob("*.py"))
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if PROVIDERS.search(line)]
    assert not hits, "provider names in core/:\n" + "\n".join(hits)

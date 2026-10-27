"""Keeps secrets out of the audit log and the dashboard.

A tool call's arguments are stored so a human can see what was attempted. Some arguments are the
secret itself (an OTP, a saved-card token, an API key); those are replaced before anything is written.
"""

from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEY = re.compile(
    r"otp|token|secret|passw|cvv|cvc|card_?n(?:o|um)|(?:^|_)pin$|api_?key|authori[sz]ation|credential", re.IGNORECASE)
REDACTED = "<redacted>"


def redact(node: Any) -> Any:
    """A copy of node with the value of every sensitive key replaced."""
    if isinstance(node, dict):
        return {k: (REDACTED if isinstance(k, str) and SENSITIVE_KEY.search(k) else redact(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [redact(v) for v in node]
    return node

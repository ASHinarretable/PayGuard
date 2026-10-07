"""Adapter registry. Interfaces pick an adapter by name; only adapters know provider details."""

from payguard.adapters.razorpay import RazorpayAdapter

ADAPTERS = {RazorpayAdapter.name: RazorpayAdapter}

__all__ = ["ADAPTERS", "RazorpayAdapter"]

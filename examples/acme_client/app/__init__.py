"""Acme customer tooling - the demo application PatchPilot migrates.

It is deliberately written the way real integration code is: a thin client
wrapper, a few reporting helpers, and some unrelated local dictionaries that
happen to be called ``customer``.
"""

__all__ = ["client", "config", "reports", "invoices", "upgrade", "legacy"]

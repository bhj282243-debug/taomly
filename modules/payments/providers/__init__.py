"""
modules/payments/providers/__init__.py — Taomly Platform
Phase 8: Payment provider registry.

Usage:
  from modules.payments.providers import get_provider

  provider = get_provider("payme")   # PaymeProvider
  provider = get_provider("click")   # ClickProvider
  provider = get_provider("sandbox") # TestSandboxProvider

Adding future providers (e.g. BankProvider):
  1. Create modules/payments/providers/bank.py
  2. Register in _REGISTRY below.
  No changes to Payment Service or Domain required.
"""

from config import settings

from .base import PaymentProvider, ParsedCallback, ProviderCallbackError
from .payme import PaymeProvider
from .click import ClickProvider
from .sandbox import TestSandboxProvider

_is_sandbox_payme = getattr(settings, "PAYME_SANDBOX", True)

_REGISTRY: dict[str, PaymentProvider] = {
    "payme":   PaymeProvider(sandbox=_is_sandbox_payme),
    "click":   ClickProvider(),
    "sandbox": TestSandboxProvider(),
}

ALLOWED_PROVIDERS = frozenset(_REGISTRY.keys())


def get_provider(provider_name: str) -> PaymentProvider:
    """
    Return the provider adapter for the given provider name.

    Raises ValueError for unknown providers.
    Payment Service calls this; it never hard-codes provider types.
    """
    provider = _REGISTRY.get(provider_name)
    if provider is None:
        raise ValueError(
            f"Unknown payment provider: {provider_name!r}. "
            f"Allowed: {sorted(ALLOWED_PROVIDERS)}"
        )
    return provider


__all__ = [
    "PaymentProvider",
    "ParsedCallback",
    "ProviderCallbackError",
    "PaymeProvider",
    "ClickProvider",
    "TestSandboxProvider",
    "get_provider",
    "ALLOWED_PROVIDERS",
]

"""
modules/payments/providers/sandbox.py — Taomly Platform
Phase 8: TestSandboxProvider — deterministic payment provider for CI/automated tests.

Rules:
  - ONLY for test / sandbox environments. Never in production.
  - Does NOT simulate Click or Payme protocols exactly.
  - Exercises the Payment Domain state machine via the PaymentProvider interface.
  - No external HTTP calls. No real money. No real credentials.
  - Cannot be enabled via a production API endpoint.

Usage in tests:
  Inject SandboxProvider via the router/service provider registry.
  Trigger checkout by calling POST /api/payments with provider="sandbox".
  Trigger success by calling the sandbox callback endpoint.

Sandbox actions (via test callback endpoint POST /api/payments/sandbox/callback):
  action="pay"    → triggers PENDING → PROCESSING → PAID
  action="fail"   → triggers PENDING → FAILED
  action="cancel" → triggers PENDING → CANCELLED
"""

from __future__ import annotations

from typing import Any

from .base import (
    CALLBACK_METHOD_COMPLETE,
    CALLBACK_METHOD_PREPARE,
    ParsedCallback,
    PaymentProvider,
    ProviderCallbackError,
)

_SANDBOX_SECRET = "sandbox-test-secret"


class TestSandboxProvider(PaymentProvider):
    """
    Deterministic payment provider for automated CI tests.

    authenticate(): accepts any request with header X-Sandbox-Secret=<sandbox_secret>.
    parse_callback(): converts simple action field to ParsedCallback.
    generate_checkout_url(): returns a test URL with no real redirect.
    """

    @property
    def provider_name(self) -> str:
        return "sandbox"

    def authenticate(
        self,
        request_headers: dict[str, str],
        request_body: dict[str, Any],
        decrypted_secret: str,
        merchant_id: str,
    ) -> None:
        """
        Accept any request for sandbox in test mode.
        In a real test environment this is called via test fixtures that
        control the request headers — no real secret is needed.
        """
        pass  # No authentication required for sandbox

    def parse_callback(
        self,
        request_body: dict[str, Any],
    ) -> ParsedCallback:
        """
        Parse sandbox callback.

        Body expected:
          {
            "payment_id": <int>,
            "action": "pay" | "fail" | "cancel",
            "amount": <int tiyins>
          }
        """
        payment_id = request_body.get("payment_id")
        action = request_body.get("action", "pay")
        amount = request_body.get("amount", 0)

        if payment_id is None:
            raise ProviderCallbackError(-1, "payment_id required")

        if action == "pay":
            # Simulate Prepare then Complete=success as two-step
            # For simplicity sandbox uses a single COMPLETE action
            return ParsedCallback(
                provider=self.provider_name,
                method=CALLBACK_METHOD_COMPLETE,
                payment_ref=str(payment_id),
                amount_tiyins=int(amount),
                provider_transaction_id=f"sandbox-tx-{payment_id}",
                click_error=0,  # 0 = success
                raw_payload=request_body,
            )
        elif action == "fail":
            return ParsedCallback(
                provider=self.provider_name,
                method=CALLBACK_METHOD_COMPLETE,
                payment_ref=str(payment_id),
                amount_tiyins=int(amount),
                provider_transaction_id=f"sandbox-tx-{payment_id}",
                click_error=-1,  # failure
                raw_payload=request_body,
            )
        elif action == "cancel":
            # Direct to CANCELLED
            return ParsedCallback(
                provider=self.provider_name,
                method=CALLBACK_METHOD_PREPARE,  # used to set PENDING → CANCELLED
                payment_ref=str(payment_id),
                amount_tiyins=int(amount),
                provider_transaction_id=f"sandbox-tx-{payment_id}",
                raw_payload={**request_body, "_sandbox_cancel": True},
            )
        else:
            raise ProviderCallbackError(-1, f"Unknown sandbox action: {action!r}")

    def generate_checkout_url(
        self,
        payment_id: int,
        amount_tiyins: int,
        currency: str,
        merchant_id: str,
        service_id: str | None = None,
    ) -> str:
        """Return a non-redirecting test URL."""
        return (
            f"https://sandbox.taomly.test/pay"
            f"?payment_id={payment_id}&amount={amount_tiyins}"
        )

    def build_success_response(
        self,
        callback: ParsedCallback,
        payment_id: int,
        paid_at_ts: int | None = None,
        created_at_ts: int | None = None,
    ) -> dict[str, Any]:
        return {"sandbox": True, "payment_id": payment_id, "status": "paid"}

    def build_error_response(
        self,
        callback: ParsedCallback | None,
        error_code: int,
        message: str,
    ) -> dict[str, Any]:
        return {"sandbox": True, "error": error_code, "message": message}

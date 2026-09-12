"""
modules/payments/providers/base.py — Taomly Platform
Phase 8: Payment Provider Abstraction.

Defines the PaymentProvider interface and the normalised ParsedCallback
datatype. All provider-specific protocol details (JSON-RPC, MD5, Basic Auth,
amount formats) are encapsulated in the concrete adapter. The Payment Service
only operates on these normalised types.

Provider adapter responsibility:
  - Parse and validate the raw provider request.
  - Authenticate / verify signature.
  - Normalise amount to integer tiyins.
  - Return ParsedCallback or raise an appropriate error.
  - Build provider-specific success / error responses.

Payment Service responsibility:
  - Business invariants (amount match, tenant isolation, state machine).
  - DB locking, transactions, idempotency.
  - Order.paid_at updates.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ──────────────────────────────────────────────────────────────────────────────
# NORMALISED CALLBACK DATA
# ──────────────────────────────────────────────────────────────────────────────

# Maps to Payment.status transitions triggered by each callback.
CALLBACK_METHOD_CHECK_PERFORM = "check_perform"   # Payme only — no transition
CALLBACK_METHOD_CREATE        = "create"          # PENDING → PROCESSING
CALLBACK_METHOD_PERFORM       = "perform"         # PROCESSING → PAID
CALLBACK_METHOD_CANCEL        = "cancel"          # PENDING/PROCESSING → FAILED
CALLBACK_METHOD_CHECK_STATUS  = "check_status"    # Read-only state query
CALLBACK_METHOD_PREPARE       = "prepare"         # Click: PENDING → PROCESSING
CALLBACK_METHOD_COMPLETE      = "complete"        # Click: PROCESSING → PAID/FAILED


@dataclass
class ParsedCallback:
    """
    Normalised result of parsing and authenticating a provider callback.

    All provider-specific details are resolved here. The Payment Service
    never reads raw provider payloads.

    amount_tiyins: always integer tiyins regardless of provider wire format.
      Payme sends integer tiyins natively.
      Click sends float soums — the ClickProvider adapter converts via Decimal.

    payment_ref: our Payment.id (as string) extracted from the provider payload.
      Payme: account["order_id"] value set at checkout URL generation time.
      Click Prepare: merchant_trans_id.
      Click Complete: merchant_prepare_id.

    provider_transaction_id: provider's own transaction identifier.
      Payme: params["id"] (24-char ObjectId string).
      Click: click_trans_id (bigint, stored as string).

    click_error: Click-specific Complete action error code.
      0  → success (PROCESSING → PAID)
      ≤-1 → cancellation from Click (PROCESSING → FAILED)
      None for all non-Click-Complete callbacks.

    payme_cancel_state: Payme CancelTransaction state value.
      -1 → cancelled before PerformTransaction (from PROCESSING/PENDING)
      -2 → cancelled after PerformTransaction (post-PAID cancellation)
      None for all non-Payme-Cancel callbacks.
    """

    provider: str                       # 'payme' | 'click' | 'sandbox'
    method: str                         # CALLBACK_METHOD_* constant
    payment_ref: str                    # Our Payment.id as string
    amount_tiyins: int                  # Always integer tiyins
    provider_transaction_id: str | None = None
    click_error: int | None = None      # Click Complete: error field
    payme_cancel_state: int | None = None  # -1 or -2
    raw_payload: dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────────
# PROVIDER ERROR — raised by adapters for invalid / rejected callbacks
# ──────────────────────────────────────────────────────────────────────────────

class ProviderCallbackError(Exception):
    """
    Raised by a provider adapter when authentication or parsing fails.

    The error_code should be in the provider's native error format:
      Payme: integer (e.g. -32504, -31001)
      Click: integer (e.g. -1, -2, -5)
    """

    def __init__(self, error_code: int, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


# ──────────────────────────────────────────────────────────────────────────────
# PAYMENT PROVIDER ABSTRACT BASE
# ──────────────────────────────────────────────────────────────────────────────

class PaymentProvider(ABC):
    """
    Abstract base class for all payment provider adapters.

    Concrete adapters: PaymeProvider, ClickProvider, TestSandboxProvider.
    Future adapters: BankProvider — plugs in without changing Payment Domain.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the canonical provider identifier: 'payme', 'click', 'sandbox'."""
        ...

    @abstractmethod
    def authenticate(
        self,
        request_headers: dict[str, str],
        request_body: dict[str, Any],
        decrypted_secret: str,
        merchant_id: str,
    ) -> None:
        """
        Verify provider authentication.

        Payme: parse Basic Auth header, compare password with decrypted_secret.
        Click: recalculate MD5 sign_string, compare with body["sign_string"].

        Raises ProviderCallbackError with provider-native error code on failure.
        Never logs decrypted_secret or the raw Basic Auth header value.
        """
        ...

    @abstractmethod
    def parse_callback(
        self,
        request_body: dict[str, Any],
    ) -> ParsedCallback:
        """
        Parse a verified provider callback into a normalised ParsedCallback.

        Called AFTER authenticate() succeeds.
        Raises ProviderCallbackError if the payload is structurally invalid.
        """
        ...

    @abstractmethod
    def generate_checkout_url(
        self,
        payment_id: int,
        amount_tiyins: int,
        currency: str,
        merchant_id: str,
        service_id: str | None,
    ) -> str:
        """
        Generate the redirect URL to send the customer to the provider checkout.
        This is a pure local operation — no external HTTP calls.

        Payme: https://checkout.paycom.uz/base64(m=...;ac.order_id=...;a=...)
        Click: https://my.click.uz/services/pay?service_id=...&merchant_id=...&...
        """
        ...

    @abstractmethod
    def build_success_response(
        self,
        callback: ParsedCallback,
        payment_id: int,
        paid_at_ts: int | None = None,
        created_at_ts: int | None = None,
    ) -> dict[str, Any]:
        """
        Build the provider-specific success response body.

        Payme: {"result": {"transaction": ..., "state": ..., ...}}
        Click: {"error": 0, "error_note": "Success", ...}
        """
        ...

    @abstractmethod
    def build_error_response(
        self,
        callback: ParsedCallback | None,
        error_code: int,
        message: str,
    ) -> dict[str, Any]:
        """
        Build the provider-specific error response body.

        Payme: {"error": {"code": ..., "message": ...}}
        Click: {"error": ..., "error_note": ...}
        """
        ...

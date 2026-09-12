"""
modules/payments/providers/payme.py — Taomly Platform
Phase 8: Payme Merchant API adapter.

Protocol: JSON-RPC 2.0. Payme calls Taomly (Taomly is the server).
Auth: Authorization: Basic base64(merchant_id:merchant_key)
Amount: integer tiyins — matches Taomly internal format directly.

Payme methods handled:
  CheckPerformTransaction — verify Order is payable (no state transition)
  CreateTransaction       — PENDING → PROCESSING
  PerformTransaction      — PROCESSING → PAID
  CancelTransaction       — PENDING/PROCESSING → FAILED  (state -1 or -2)
  CheckTransaction        — read-only state query

Idempotency requirement (official Payme docs):
  Repeated calls to CreateTransaction / PerformTransaction / CancelTransaction
  MUST return the same result as the first call.

Error codes (official developer.help.paycom.uz):
  General:
    -32300 method not POST
    -32700 JSON parse error
    -32600 missing required fields
    -32601 method not found
    -32504 insufficient privileges (auth failure)
    -32400 system error
  Method-specific:
    -31001 incorrect amount
    -31003 transaction not found
    -31007 cannot cancel — order already fulfilled
    -31008 cannot perform operation (wrong state)
    -31050…-31099 account/order errors (order not found, etc.)

Checkout URL (GET method, official docs):
  https://checkout.paycom.uz/base64(m=<merchant_id>;ac.order_id=<payment_id>;a=<tiyins>)

Payme account field convention for Taomly:
  Field name: "order_id"  (registered at Payme cashier setup)
  Value: Payment.id
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any

from .base import (
    CALLBACK_METHOD_CANCEL,
    CALLBACK_METHOD_CHECK_PERFORM,
    CALLBACK_METHOD_CHECK_STATUS,
    CALLBACK_METHOD_CREATE,
    CALLBACK_METHOD_PERFORM,
    ParsedCallback,
    PaymentProvider,
    ProviderCallbackError,
)

logger = logging.getLogger(__name__)

# ── Official Payme error codes ─────────────────────────────────────────────
_E_METHOD_NOT_POST   = -32300
_E_PARSE             = -32700
_E_MISSING_FIELDS    = -32600
_E_METHOD_NOT_FOUND  = -32601
_E_AUTH              = -32504  # Insufficient privileges
_E_SYSTEM            = -32400
_E_AMOUNT            = -31001  # Incorrect amount
_E_TX_NOT_FOUND      = -31003  # Transaction not found
_E_CANNOT_CANCEL     = -31007  # Order already fulfilled, cannot cancel
_E_CANNOT_PERFORM    = -31008  # Cannot perform operation (wrong state)
_E_ACCOUNT           = -31050  # Account/order not found (range -31050..-31099)

# Payme transaction states
_STATE_CREATED   = 1   # CreateTransaction success
_STATE_PERFORMED = 2   # PerformTransaction success
_STATE_CANCELLED_BEFORE_PERFORM = -1   # Cancelled from state 1
_STATE_CANCELLED_AFTER_PERFORM  = -2   # Cancelled from state 2

# Payme method names (exact strings from docs)
_METHOD_CHECK_PERFORM = "CheckPerformTransaction"
_METHOD_CREATE        = "CreateTransaction"
_METHOD_PERFORM       = "PerformTransaction"
_METHOD_CANCEL        = "CancelTransaction"
_METHOD_CHECK         = "CheckTransaction"

# Checkout hosts
_CHECKOUT_PROD    = "https://checkout.paycom.uz"
_CHECKOUT_SANDBOX = "https://checkout.test.paycom.uz"


class PaymeProvider(PaymentProvider):
    """
    Payme Merchant API adapter.

    Pass sandbox=True to use the Payme test checkout host.
    The merchant_key is decrypted by the Payment Service before calling
    authenticate() — this adapter never accesses the database or Fernet.
    """

    def __init__(self, sandbox: bool = False) -> None:
        self._sandbox = sandbox

    @property
    def provider_name(self) -> str:
        return "payme"

    # ── Authentication ─────────────────────────────────────────────────────

    def authenticate(
        self,
        request_headers: dict[str, str],
        request_body: dict[str, Any],
        decrypted_secret: str,
        merchant_id: str,
    ) -> None:
        """
        Verify Payme Basic Auth.

        Header: Authorization: Basic base64(merchant_id:merchant_key)
        We compare the decoded password against decrypted_secret.

        NEVER log the raw Authorization header, decoded password, or
        decrypted_secret.
        """
        auth_header = request_headers.get("authorization", "")
        if not auth_header.lower().startswith("basic "):
            raise ProviderCallbackError(
                _E_AUTH,
                "Missing or invalid Authorization header",
            )

        try:
            encoded = auth_header[6:]  # strip "Basic "
            decoded = base64.b64decode(encoded).decode("utf-8")
            _username, password = decoded.split(":", 1)
        except Exception:
            raise ProviderCallbackError(_E_AUTH, "Malformed Basic Auth header")

        # Constant-time comparison to resist timing attacks
        if not hmac.compare_digest(password, decrypted_secret):
            raise ProviderCallbackError(_E_AUTH, "Invalid merchant credentials")

    # ── Callback parsing ───────────────────────────────────────────────────

    def parse_callback(
        self,
        request_body: dict[str, Any],
    ) -> ParsedCallback:
        """
        Parse JSON-RPC 2.0 payload into a normalised ParsedCallback.

        The 'method' field determines which transaction lifecycle step this is.
        The 'params.account.order_id' value is our Payment.id.
        """
        method = request_body.get("method", "")
        params = request_body.get("params", {})

        if method == _METHOD_CHECK_PERFORM:
            return self._parse_check_perform(params, request_body)
        elif method == _METHOD_CREATE:
            return self._parse_create(params, request_body)
        elif method == _METHOD_PERFORM:
            return self._parse_perform(params, request_body)
        elif method == _METHOD_CANCEL:
            return self._parse_cancel(params, request_body)
        elif method == _METHOD_CHECK:
            return self._parse_check(params, request_body)
        else:
            raise ProviderCallbackError(_E_METHOD_NOT_FOUND, f"Unknown method: {method!r}")

    def _extract_payment_id_from_account(self, params: dict[str, Any]) -> str:
        """Extract our Payment.id from Payme account.order_id field."""
        account = params.get("account", {})
        order_id = account.get("order_id")
        if order_id is None:
            raise ProviderCallbackError(
                _E_ACCOUNT,
                "account.order_id is required",
            )
        return str(order_id)

    def _parse_check_perform(
        self, params: dict[str, Any], raw: dict
    ) -> ParsedCallback:
        amount = params.get("amount")
        if not isinstance(amount, int) or amount <= 0:
            raise ProviderCallbackError(_E_AMOUNT, "Invalid amount in params")
        payment_ref = self._extract_payment_id_from_account(params)
        return ParsedCallback(
            provider=self.provider_name,
            method=CALLBACK_METHOD_CHECK_PERFORM,
            payment_ref=payment_ref,
            amount_tiyins=amount,
            raw_payload=raw,
        )

    def _parse_create(
        self, params: dict[str, Any], raw: dict
    ) -> ParsedCallback:
        payme_tx_id = params.get("id")
        amount = params.get("amount")
        if not payme_tx_id or not isinstance(amount, int) or amount <= 0:
            raise ProviderCallbackError(_E_MISSING_FIELDS, "id and amount required")
        payment_ref = self._extract_payment_id_from_account(params)
        return ParsedCallback(
            provider=self.provider_name,
            method=CALLBACK_METHOD_CREATE,
            payment_ref=payment_ref,
            amount_tiyins=amount,
            provider_transaction_id=str(payme_tx_id),
            raw_payload=raw,
        )

    def _parse_perform(
        self, params: dict[str, Any], raw: dict
    ) -> ParsedCallback:
        payme_tx_id = params.get("id")
        if not payme_tx_id:
            raise ProviderCallbackError(_E_MISSING_FIELDS, "id required")
        return ParsedCallback(
            provider=self.provider_name,
            method=CALLBACK_METHOD_PERFORM,
            payment_ref="",  # resolved by provider_transaction_id lookup
            amount_tiyins=0,  # not provided in PerformTransaction params
            provider_transaction_id=str(payme_tx_id),
            raw_payload=raw,
        )

    def _parse_cancel(
        self, params: dict[str, Any], raw: dict
    ) -> ParsedCallback:
        payme_tx_id = params.get("id")
        if not payme_tx_id:
            raise ProviderCallbackError(_E_MISSING_FIELDS, "id required")
        return ParsedCallback(
            provider=self.provider_name,
            method=CALLBACK_METHOD_CANCEL,
            payment_ref="",  # resolved by provider_transaction_id lookup
            amount_tiyins=0,
            provider_transaction_id=str(payme_tx_id),
            raw_payload=raw,
        )

    def _parse_check(
        self, params: dict[str, Any], raw: dict
    ) -> ParsedCallback:
        payme_tx_id = params.get("id")
        if not payme_tx_id:
            raise ProviderCallbackError(_E_MISSING_FIELDS, "id required")
        return ParsedCallback(
            provider=self.provider_name,
            method=CALLBACK_METHOD_CHECK_STATUS,
            payment_ref="",
            amount_tiyins=0,
            provider_transaction_id=str(payme_tx_id),
            raw_payload=raw,
        )

    # ── Response builders ──────────────────────────────────────────────────

    def build_success_response(
        self,
        callback: ParsedCallback,
        payment_id: int,
        paid_at_ts: int | None = None,
        created_at_ts: int | None = None,
    ) -> dict[str, Any]:
        """
        Build JSON-RPC result for a successful operation.
        transaction = str(payment_id) — our merchant transaction reference.
        """
        now_ms = self._now_ms()
        method = callback.method

        if method == CALLBACK_METHOD_CHECK_PERFORM:
            return {"result": {"allow": True}}

        elif method == CALLBACK_METHOD_CREATE:
            return {
                "result": {
                    "create_time": created_at_ts or now_ms,
                    "transaction": str(payment_id),
                    "state": _STATE_CREATED,
                }
            }

        elif method == CALLBACK_METHOD_PERFORM:
            return {
                "result": {
                    "transaction": str(payment_id),
                    "perform_time": paid_at_ts or now_ms,
                    "state": _STATE_PERFORMED,
                }
            }

        elif method == CALLBACK_METHOD_CANCEL:
            cancel_state = (
                callback.payme_cancel_state
                if callback.payme_cancel_state is not None
                else _STATE_CANCELLED_BEFORE_PERFORM
            )
            return {
                "result": {
                    "transaction": str(payment_id),
                    "cancel_time": now_ms,
                    "state": cancel_state,
                }
            }

        elif method == CALLBACK_METHOD_CHECK_STATUS:
            return {
                "result": {
                    "create_time": created_at_ts or 0,
                    "perform_time": paid_at_ts or 0,
                    "cancel_time": 0,
                    "transaction": str(payment_id),
                    "state": _STATE_PERFORMED if paid_at_ts else _STATE_CREATED,
                    "reason": None,
                }
            }

        # Fallback
        return {"result": {"transaction": str(payment_id)}}

    def build_error_response(
        self,
        callback: ParsedCallback | None,
        error_code: int,
        message: str,
    ) -> dict[str, Any]:
        """Build JSON-RPC error response."""
        rpc_id = None
        if callback and callback.raw_payload:
            rpc_id = callback.raw_payload.get("id")
        response: dict[str, Any] = {
            "error": {
                "code": error_code,
                "message": {"ru": message, "uz": message, "en": message},
            }
        }
        if rpc_id is not None:
            response["id"] = rpc_id
        return response

    def build_account_error_response(
        self,
        callback: ParsedCallback | None,
        field_name: str,
        message: str,
    ) -> dict[str, Any]:
        """Build -31050 account error with required 'data' field pointing to account subfield."""
        rpc_id = callback.raw_payload.get("id") if callback else None
        response: dict[str, Any] = {
            "error": {
                "code": _E_ACCOUNT,
                "message": {"ru": message, "uz": message, "en": message},
                "data": field_name,
            }
        }
        if rpc_id is not None:
            response["id"] = rpc_id
        return response

    # ── Checkout URL ───────────────────────────────────────────────────────

    def generate_checkout_url(
        self,
        payment_id: int,
        amount_tiyins: int,
        currency: str,
        merchant_id: str,
        service_id: str | None = None,
    ) -> str:
        """
        Generate Payme checkout GET URL.

        Format: https://checkout.paycom.uz/base64(m=<id>;ac.order_id=<pid>;a=<tiyins>)
        account field "order_id" is registered as the cashier's account field
        in the Payme merchant cabinet.
        Amount: tiyins (integer) — no conversion needed.
        """
        host = _CHECKOUT_SANDBOX if self._sandbox else _CHECKOUT_PROD
        params_str = (
            f"m={merchant_id};"
            f"ac.order_id={payment_id};"
            f"a={amount_tiyins}"
        )
        encoded = base64.b64encode(params_str.encode()).decode()
        return f"{host}/{encoded}"

    # ── Public error code constants (for Payment Service use) ──────────────

    @property
    def E_AUTH(self) -> int:
        return _E_AUTH

    @property
    def E_AMOUNT(self) -> int:
        return _E_AMOUNT

    @property
    def E_TX_NOT_FOUND(self) -> int:
        return _E_TX_NOT_FOUND

    @property
    def E_CANNOT_CANCEL(self) -> int:
        return _E_CANNOT_CANCEL

    @property
    def E_CANNOT_PERFORM(self) -> int:
        return _E_CANNOT_PERFORM

    @property
    def E_ACCOUNT(self) -> int:
        return _E_ACCOUNT

    @property
    def E_SYSTEM(self) -> int:
        return _E_SYSTEM

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _now_ms() -> int:
        """Current UTC time in milliseconds (Payme Timestamp format)."""
        return int(datetime.now(timezone.utc).timestamp() * 1000)

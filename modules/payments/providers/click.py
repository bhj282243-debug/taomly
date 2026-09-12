"""
modules/payments/providers/click.py — Taomly Platform
Phase 8: Click SHOP API adapter.

Protocol: HTTP POST. Click calls Taomly (Taomly is the server).
Two actions: Prepare (action=0) and Complete (action=1).
Auth: MD5 sign_string (no auth header in SHOP API callbacks).
Amount: float soums from Click — MUST be converted to integer tiyins via Decimal.

Click identifiers:
  service_id        — Click's ID for the merchant service. In callback routing.
                      Entered in sign_string. Stored in RestaurantPaymentConfig.service_id.
  merchant_id       — Click's ID for the merchant account. Used in checkout URL only.
                      Stored in RestaurantPaymentConfig.merchant_id.
  click_trans_id    — Click's transaction identifier (bigint). Our provider_transaction_id.
  merchant_trans_id — Our Payment.id as varchar. Sent in Prepare; echoed in Complete.
  merchant_prepare_id — Our Payment.id as int. Returned in Prepare; sent in Complete.

Sign string formulas (official docs.click.uz):
  Prepare:
    MD5(click_trans_id + service_id + SECRET_KEY + merchant_trans_id
        + amount + action + sign_time)
  Complete:
    MD5(click_trans_id + service_id + SECRET_KEY + merchant_trans_id
        + merchant_prepare_id + amount + action + sign_time)

All values concatenated WITHOUT separator before hashing.

Error codes (official docs.click.uz/en/click-api-error/):
   0  Success
  -1  SIGN CHECK FAILED (invalid signature)
  -2  Incorrect parameter amount
  -3  Action not found
  -4  Already paid
  -5  User/order not found (check merchant_trans_id)
  -6  Transaction not found (check merchant_prepare_id)
  -7  Failed to update user
  -8  Error in request from CLICK
  -9  Transaction cancelled

Checkout URL (official docs.click.uz/en/mobile-integration/):
  https://my.click.uz/services/pay
    ?service_id=<service_id>
    &merchant_id=<merchant_id>
    &amount=<amount_soums>
    &transaction_param=<payment_id>
  Optional: return_url, merchant_user_id
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .base import (
    CALLBACK_METHOD_COMPLETE,
    CALLBACK_METHOD_PREPARE,
    ParsedCallback,
    PaymentProvider,
    ProviderCallbackError,
)

logger = logging.getLogger(__name__)

# ── Official Click error codes ─────────────────────────────────────────────
_E_SIGN       = -1   # Signature check failed
_E_AMOUNT     = -2   # Incorrect amount
_E_ACTION     = -3   # Action not found
_E_PAID       = -4   # Already paid
_E_NOT_FOUND  = -5   # Order/user not found
_E_TX_MISSING = -6   # Transaction (merchant_prepare_id) not found
_E_UPDATE     = -7   # Failed to update
_E_REQUEST    = -8   # Bad request from Click
_E_CANCELLED  = -9   # Transaction cancelled

_CHECKOUT_HOST = "https://my.click.uz/services/pay"


class ClickProvider(PaymentProvider):
    """
    Click SHOP API adapter.

    Amount conversion:
      Click sends float soums. We MUST use Decimal, not float arithmetic,
      to avoid floating-point representation errors when converting to tiyins:
        amount_tiyins = int(Decimal(str(soums_float)) * 100)
    """

    @property
    def provider_name(self) -> str:
        return "click"

    # ── Authentication ─────────────────────────────────────────────────────

    def authenticate(
        self,
        request_headers: dict[str, str],
        request_body: dict[str, Any],
        decrypted_secret: str,
        merchant_id: str,
    ) -> None:
        """
        Verify Click MD5 sign_string.

        Prepare sign:  MD5(click_trans_id + service_id + SECRET + merchant_trans_id
                           + amount + action + sign_time)
        Complete sign: MD5(click_trans_id + service_id + SECRET + merchant_trans_id
                           + merchant_prepare_id + amount + action + sign_time)

        NEVER log decrypted_secret or the computed expected_sign.
        """
        received_sign = request_body.get("sign_string", "")
        action = str(request_body.get("action", ""))
        click_trans_id = str(request_body.get("click_trans_id", ""))
        service_id = str(request_body.get("service_id", ""))
        merchant_trans_id = str(request_body.get("merchant_trans_id", ""))
        amount = str(request_body.get("amount", ""))
        sign_time = str(request_body.get("sign_time", ""))

        if action == "0":
            # Prepare
            raw = (
                click_trans_id
                + service_id
                + decrypted_secret
                + merchant_trans_id
                + amount
                + action
                + sign_time
            )
        elif action == "1":
            # Complete — merchant_prepare_id is added
            merchant_prepare_id = str(request_body.get("merchant_prepare_id", ""))
            raw = (
                click_trans_id
                + service_id
                + decrypted_secret
                + merchant_trans_id
                + merchant_prepare_id
                + amount
                + action
                + sign_time
            )
        else:
            raise ProviderCallbackError(_E_ACTION, f"Unknown action: {action!r}")

        expected_sign = hashlib.md5(raw.encode("utf-8")).hexdigest()

        # Constant-time comparison
        if not hmac.compare_digest(received_sign, expected_sign):
            raise ProviderCallbackError(_E_SIGN, "SIGN CHECK FAILED!")

    # ── Amount conversion ──────────────────────────────────────────────────

    @staticmethod
    def soums_to_tiyins(amount_soums: Any) -> int:
        """
        Convert Click float soums to integer tiyins.

        NEVER use float * 100 — floating-point representation errors.
        Use Decimal(str(value)) * 100 for safe conversion.

        Example: 10000.0 soums → 1000000 tiyins
        """
        try:
            d = Decimal(str(amount_soums)) * 100
            return int(d.to_integral_value(rounding=ROUND_HALF_UP))
        except Exception as exc:
            raise ProviderCallbackError(
                _E_AMOUNT, f"Cannot convert amount to tiyins: {amount_soums!r}"
            ) from exc

    # ── Callback parsing ───────────────────────────────────────────────────

    def parse_callback(
        self,
        request_body: dict[str, Any],
    ) -> ParsedCallback:
        """
        Parse Click SHOP API callback (Prepare or Complete).
        authenticate() must be called before this.
        """
        action = str(request_body.get("action", ""))

        if action == "0":
            return self._parse_prepare(request_body)
        elif action == "1":
            return self._parse_complete(request_body)
        else:
            raise ProviderCallbackError(_E_ACTION, f"Unknown action: {action!r}")

    def _parse_prepare(self, body: dict[str, Any]) -> ParsedCallback:
        """
        Parse Prepare (action=0).
        merchant_trans_id = our Payment.id (as string).
        """
        merchant_trans_id = body.get("merchant_trans_id")
        click_trans_id = body.get("click_trans_id")
        amount_soums = body.get("amount")

        if merchant_trans_id is None or click_trans_id is None or amount_soums is None:
            raise ProviderCallbackError(_E_REQUEST, "Missing required Prepare fields")

        amount_tiyins = self.soums_to_tiyins(amount_soums)

        return ParsedCallback(
            provider=self.provider_name,
            method=CALLBACK_METHOD_PREPARE,
            payment_ref=str(merchant_trans_id),
            amount_tiyins=amount_tiyins,
            provider_transaction_id=str(click_trans_id),
            raw_payload=body,
        )

    def _parse_complete(self, body: dict[str, Any]) -> ParsedCallback:
        """
        Parse Complete (action=1).
        merchant_prepare_id = our Payment.id (as int/string).
        click_error = body["error"] (0=success, ≤-1=cancellation from Click).
        """
        merchant_prepare_id = body.get("merchant_prepare_id")
        click_trans_id = body.get("click_trans_id")
        amount_soums = body.get("amount")
        click_error = body.get("error", 0)

        if merchant_prepare_id is None or click_trans_id is None:
            raise ProviderCallbackError(_E_REQUEST, "Missing required Complete fields")

        amount_tiyins = self.soums_to_tiyins(amount_soums) if amount_soums is not None else 0

        return ParsedCallback(
            provider=self.provider_name,
            method=CALLBACK_METHOD_COMPLETE,
            payment_ref=str(merchant_prepare_id),
            amount_tiyins=amount_tiyins,
            provider_transaction_id=str(click_trans_id),
            click_error=int(click_error),
            raw_payload=body,
        )

    # ── Response builders ──────────────────────────────────────────────────

    def build_success_response(
        self,
        callback: ParsedCallback,
        payment_id: int,
        paid_at_ts: int | None = None,
        created_at_ts: int | None = None,
    ) -> dict[str, Any]:
        """Build Click success response (error=0)."""
        merchant_trans_id = callback.raw_payload.get("merchant_trans_id", "")

        if callback.method == CALLBACK_METHOD_PREPARE:
            return {
                "click_trans_id": callback.raw_payload.get("click_trans_id"),
                "merchant_trans_id": merchant_trans_id,
                "merchant_prepare_id": payment_id,
                "error": 0,
                "error_note": "Success",
            }
        elif callback.method == CALLBACK_METHOD_COMPLETE:
            return {
                "click_trans_id": callback.raw_payload.get("click_trans_id"),
                "merchant_trans_id": merchant_trans_id,
                "merchant_confirm_id": payment_id,
                "error": 0,
                "error_note": "Success",
            }
        return {"error": 0, "error_note": "Success"}

    def build_error_response(
        self,
        callback: ParsedCallback | None,
        error_code: int,
        message: str,
    ) -> dict[str, Any]:
        """Build Click error response."""
        response: dict[str, Any] = {
            "error": error_code,
            "error_note": message,
        }
        if callback and callback.raw_payload:
            response["click_trans_id"] = callback.raw_payload.get("click_trans_id")
            response["merchant_trans_id"] = callback.raw_payload.get("merchant_trans_id")
        return response

    # ── Checkout URL ───────────────────────────────────────────────────────

    def generate_checkout_url(
        self,
        payment_id: int,
        amount_tiyins: int,
        currency: str,
        merchant_id: str,
        service_id: str | None,
    ) -> str:
        """
        Generate Click checkout URL.

        Amount in URL: soums (float), derived from tiyins / 100.
        transaction_param = our Payment.id.
        """
        if not service_id:
            raise ValueError("Click requires service_id for checkout URL generation")

        # Convert tiyins back to soums for the Click URL
        amount_soums = Decimal(amount_tiyins) / 100
        # Format without trailing zeros where possible
        amount_str = f"{amount_soums:f}".rstrip("0").rstrip(".")
        if "." not in amount_str:
            amount_str = amount_str  # integer amount

        return (
            f"{_CHECKOUT_HOST}"
            f"?service_id={service_id}"
            f"&merchant_id={merchant_id}"
            f"&amount={amount_str}"
            f"&transaction_param={payment_id}"
        )

    # ── Public error code constants ────────────────────────────────────────

    @property
    def E_SIGN(self) -> int:
        return _E_SIGN

    @property
    def E_AMOUNT(self) -> int:
        return _E_AMOUNT

    @property
    def E_PAID(self) -> int:
        return _E_PAID

    @property
    def E_NOT_FOUND(self) -> int:
        return _E_NOT_FOUND

    @property
    def E_TX_MISSING(self) -> int:
        return _E_TX_MISSING

    @property
    def E_CANCELLED(self) -> int:
        return _E_CANCELLED

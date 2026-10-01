"""Small, credential-safe client for YooKassa's external checkout API.

The caller persists a local order before calling ``create_payment``.  The
checkout reference is reused as YooKassa's idempotence key on retries.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx


API_BASE_URL = "https://api.yookassa.ru/v3"


class YooKassaGatewayError(Exception):
    """A remote payment request failed or returned an unusable response."""


@dataclass(frozen=True)
class YooKassaPayment:
    id: str
    status: str
    paid: bool
    amount: Decimal
    currency: str
    checkout_ref: str | None
    confirmation_url: str | None


def _parse_payment(data: Mapping[str, Any]) -> YooKassaPayment:
    try:
        amount_data = data["amount"]
        confirmation = data.get("confirmation") or {}
        metadata = data.get("metadata") or {}
        payment_id = data["id"]
        status = data["status"]
        amount = Decimal(str(amount_data["value"]))
        currency = amount_data["currency"]
        checkout_ref = metadata.get("checkout_ref")
        confirmation_url = confirmation.get("confirmation_url")
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise YooKassaGatewayError("ЮKassa вернула неполные данные платежа") from exc

    if (
        not isinstance(payment_id, str)
        or not payment_id
        or not isinstance(status, str)
        or not isinstance(currency, str)
        or not amount.is_finite()
        or amount <= 0
        or (checkout_ref is not None and not isinstance(checkout_ref, str))
    ):
        raise YooKassaGatewayError("ЮKassa вернула некорректные данные платежа")
    if confirmation_url is not None:
        if not isinstance(confirmation_url, str):
            raise YooKassaGatewayError("ЮKassa вернула некорректную ссылку оплаты")
        parsed = urlsplit(confirmation_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise YooKassaGatewayError("ЮKassa вернула небезопасную ссылку оплаты")

    return YooKassaPayment(
        id=payment_id,
        status=status,
        paid=data.get("paid") is True,
        amount=amount,
        currency=currency,
        checkout_ref=checkout_ref,
        confirmation_url=confirmation_url,
    )


class YooKassaClient:
    """Calls YooKassa without exposing credentials or provider response bodies in errors."""

    def __init__(
        self,
        shop_id: str,
        secret_key: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not shop_id or not secret_key:
            raise ValueError("ЮKassa credentials are required")
        self._shop_id = shop_id
        self._secret_key = secret_key
        self._client = client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> YooKassaPayment:
        headers = {"Idempotence-Key": idempotency_key} if idempotency_key else {}
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0), follow_redirects=False, trust_env=False
        )
        try:
            # Retry connection failures with the same Idempotence-Key. No HTTP
            # request is accepted on a failed connect/TLS handshake, and the
            # stable key also protects an uncertain provider-side outcome.
            for attempt in range(3):
                try:
                    response = await client.request(
                        method,
                        API_BASE_URL + path,
                        auth=(self._shop_id, self._secret_key),
                        headers=headers,
                        json=body,
                    )
                    break
                except (httpx.ConnectTimeout, httpx.ConnectError):
                    if attempt == 2:
                        raise
            if response.status_code < 200 or response.status_code >= 300:
                raise YooKassaGatewayError(
                    f"Запрос ЮKassa не выполнен (HTTP {response.status_code})"
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise YooKassaGatewayError("ЮKassa вернула некорректный ответ") from exc
            if not isinstance(data, dict):
                raise YooKassaGatewayError("ЮKassa вернула некорректный ответ")
            return _parse_payment(data)
        except httpx.HTTPError:
            # Transport exceptions may contain request context. Keep the
            # credential-bearing HTTP request out of upstream tracebacks.
            raise YooKassaGatewayError("Не удалось связаться с ЮKassa") from None
        finally:
            if owns_client:
                await client.aclose()

    async def create_payment(
        self,
        *,
        checkout_ref: str,
        amount: Decimal,
        currency: str,
        description: str,
        return_url: str,
        receipt: dict[str, Any] | None = None,
    ) -> YooKassaPayment:
        parsed_return = urlsplit(return_url)
        if (
            parsed_return.scheme != "https"
            or not parsed_return.hostname
            or parsed_return.username
            or parsed_return.password
        ):
            raise ValueError("HTTPS return_url is required")
        if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
            raise ValueError("Invalid payment amount")
        body: dict[str, Any] = {
            "amount": {"value": f"{amount:.2f}", "currency": currency},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": description[:128],
            "metadata": {"checkout_ref": checkout_ref},
        }
        if receipt is not None:
            body["receipt"] = receipt
        return await self._request(
            "POST", "/payments", body=body, idempotency_key=checkout_ref
        )

    async def get_payment(self, payment_id: str) -> YooKassaPayment:
        # YooKassa IDs are UUID-like. Restrict path characters before interpolation.
        if not payment_id or len(payment_id) > 128 or not all(
            c.isascii() and (c.isalnum() or c in "-_") for c in payment_id
        ):
            raise ValueError("Invalid YooKassa payment id")
        return await self._request("GET", f"/payments/{payment_id}")

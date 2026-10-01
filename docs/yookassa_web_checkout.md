# YooKassa SaaS checkout on the ZapisFlow website

The dedicated billing host at `https://pay.zapisflow.su` serves the checkout
page. The existing `https://zapisflow.su` site remains on its current hosting.
The API host remains `https://api.zapisflow.su`; configure its YooKassa notification
URL as `https://api.zapisflow.su/billing/yookassa/webhook`. This channel is for
ZapisFlow SaaS subscriptions only. Customer payments for a master's services
remain separate. Telegram invoices and Stars are separate payment channels;
this implementation does not use Telegram Payments.

## Flow

1. The Manager Bot checks the project owner and reads the active plan from
   PostgreSQL. In the Manager update transaction it creates one
   `SubscriptionPayment` with an immutable amount/period snapshot and one
   `CheckoutSession` with a 15-minute expiry. The URL is sent only after that
   transaction commits.
2. The checkout URL contains a 32-byte random bearer token. PostgreSQL stores
   only its SHA-256 digest. The page displays the saved payment amount and
   duration, asks for an email for the fiscal receipt, and sends no plan, user,
   payment ID, or amount from the browser to the payment service.
3. Submitting the form atomically marks the session used. The backend builds
   receipt data from the email, payment snapshot and merchant-confirmed fiscal
   settings, then creates a YooKassa payment with the payment's stable
   idempotence key. A second form submission cannot create another order.
4. The browser goes to YooKassa's HTTPS confirmation URL. Returning to
   `/billing/success` is informational and never activates a subscription.
   The webhook fetches the current payment from YooKassa using shop credentials,
   verifies the reference, amount and currency, then calls the existing
   idempotent `SubscriptionService`. The reconciliation job covers missed
   webhook notifications. A single payment creates at most one period.

The token is a bearer capability: anyone who obtains the URL before it is used
can act on that order. The URL contains no predictable user or payment ID and
expires quickly. Avoid forwarding it. The checkout URL is excluded from Caddy
access logs; Uvicorn access logging is disabled for the webhook application.
The browser receives `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.
Without a separate login, the website cannot identify the person holding a
copied link. It cannot move the payment to another tenant through form fields.

## Environment and merchant setup

Keep `PAYMENT_PROVIDER=disabled` until test credentials, fiscal values, DNS and
TLS are verified. Never commit real shop secrets. Required when enabled:

* `PAYMENT_PROVIDER=yookassa_web`
* `YOOKASSA_MODE=test` for test credentials or `live` for production
* `YOOKASSA_TEST_SHOP_ID`, `YOOKASSA_TEST_SECRET_KEY` for the test shop
* `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY` for the live shop
* `PAYMENT_CURRENCY=RUB`
* `BILLING_DOMAIN=pay.zapisflow.su` for Caddy
* `BILLING_RETURN_URL=https://pay.zapisflow.su/billing/success`
* `YOOKASSA_RECEIPT_VAT_CODE`
* `YOOKASSA_RECEIPT_PAYMENT_SUBJECT`
* `YOOKASSA_RECEIPT_PAYMENT_MODE`
* `SUPPORT_TELEGRAM_USERNAME=zapisflow`

The merchant must confirm the VAT code and fiscal item attributes against the
shop's «Чеки от ЮKassa» setup. The site collects an actual customer email for
the receipt. No tax code, email, or item attribute is invented in production.
The backend fails startup with `PAYMENT_PROVIDER=yookassa_web` if the fiscal
settings or selected shop credentials are missing. YooKassa receipt fields are
described in the [official receipt documentation](https://yookassa.ru/developers/payment-acceptance/receipts/54fz/yoomoney/payments).

## Recovery and verification

The provider call happens after the local order commits. Repeated provider
requests for the same payment use the same key within YooKassa's idempotence
window; after 23 hours, an uncertain order is not submitted again. A used
checkout token cannot be used again, even if the external API fails. In that
case the customer requests a new link or contacts support, while webhook and
reconciliation continue checking the original order. Operators should inspect
uncertain orders before asking customers to pay again.

Before production activation: apply migration `0017`, validate Caddy and TLS
for both domains, run a real test-shop payment with a receipt, verify webhook
activation and duplicate notification behavior, then configure the live shop
and enable `PAYMENT_PROVIDER=yookassa_web`. Do not treat a browser return as
proof of payment.

Create an `A` record for `pay.zapisflow.su` pointing to `185.221.23.193`.
If an `AAAA` record is present, point it to a working IPv6 address on the same
VPS or remove it. Leave the `zapisflow.su` DNS records and OpenResty hosting
unchanged. After DNS propagation, Caddy will obtain a separate certificate for
`pay.zapisflow.su`. Verify TLS and the checkout routes before enabling payment.

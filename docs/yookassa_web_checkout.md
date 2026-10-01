# YooKassa SaaS billing

The Manager Bot reads the active plan and owner from PostgreSQL. It commits one
pending `SubscriptionPayment` with the plan's amount, currency and duration as
a snapshot, then uses the existing YooKassa client to create a redirect payment.
The bot sends the provider's `confirmation_url` as a URL button. It does not
create a checkout session or send the customer through `pay.zapisflow.su`.
The old checkout endpoints and migration 0017 remain for compatibility; in
self-employed mode those links return 410 and cannot submit a fiscal receipt.
The main `zapisflow.su` site and its DNS are unaffected.

The POST to YooKassa uses the local `checkout_ref` as `Idempotence-Key` and
includes local payment, user and plan IDs in metadata. A repeated click reuses
the pending local payment; if YooKassa has already created it, the service GETs
that payment and reuses its confirmation URL. Unknown outcomes older than 23
hours require operator review because YooKassa's key expires after 24 hours.
Concurrent callbacks may issue the same POST key, but cannot create another
local payment for that project and plan. No Telegram or browser parameter
sets the amount.

`https://api.zapisflow.su/billing/yookassa/return` is informational. Only
`POST /billing/yookassa/webhook` and the reconciliation job can process a
successful payment after an authenticated GET to YooKassa verifies provider ID,
reference, amount, currency and metadata. The existing subscription service
and unique payment-to-period link prevent a replay from extending twice.

## Configuration

Keep credentials in the VPS `.env`, never in Git or logs:

* `PAYMENT_PROVIDER=yookassa_web`
* `YOOKASSA_MODE=test` with `YOOKASSA_TEST_SHOP_ID` and `YOOKASSA_TEST_SECRET_KEY`
* `YOOKASSA_ALLOW_TEST_IN_PRODUCTION=true` only for a controlled test
* `YOOKASSA_TEST_ALLOWED_TELEGRAM_IDS=[...]` for test owners
* `PAYMENT_CURRENCY=RUB`
* `BILLING_RETURN_URL=https://api.zapisflow.su/billing/yookassa/return`
* `YOOKASSA_FISCAL_MODE=self_employed`

The existing live mode selects a separate credential pair. The production test
opt-in remains mandatory. `pay.zapisflow.su` stays configured in Caddy but is
not required for the direct payment path.

## Self-employed receipts

The ZapisFlow owner uses НПД. In `self_employed` mode the payment request has
no YooKassa `receipt` and no email requirement. **After receiving payment,
register the income and issue the buyer's receipt in «Мой налог».** This is an
operator task; the project does not automate «Мой налог». YooKassa's ordinary
electronic payment confirmation is not this fiscal receipt. See the
[YooKassa receipt documentation](https://yookassa.ru/developers/payment-acceptance/receipts/basics).
The old `merchant_receipt` mode remains available only for a differently
configured merchant and still requires explicit VAT and item settings.

## Release blockers

Run a test-shop payment and verify the webhook, one period, and replay. Do not
enable live payments until external access to `api.zapisflow.su` is reliable,
the merchant configuration is verified, and the Telegram distribution policy
is resolved. [Telegram's digital goods rules](https://core.telegram.org/bots/payments-stars)
say that sales of digital services inside bots must use Stars, including when
an external payment portal exists. This direct URL button is for controlled
testing only and should not be described as compliant for live sales.

# External YooKassa checkout integration

This repository contains the billing order service and the provider webhook for
an independently accessed website. It does not contain that website or its
login system. Keep `PAYMENT_PROVIDER=disabled` until the website can call the
service with an authenticated local `User.id` and can present the returned
redirect to the same owner. Do not put the checkout URL in Manager Bot.

## Website handoff

After the site's existing login has identified a ZapisFlow owner, its server
must map that login to the local `User.id`. It may then call
`YooKassaCheckoutService.create_order(actor_user_id, master_id, plan_code)`.
The service checks ownership and reads the active plan's amount, currency, and
duration from PostgreSQL. It commits a `PENDING` order with an opaque UUID.
Next call `start_checkout(checkout_ref, actor_user_id)`; only the authenticated
owner should receive `confirmation_url`. The website must not trust a browser
supplied user ID, price, subscription status, or return URL. A redirect back to
the website is informational; it never activates the subscription.

The provider sends `payment.succeeded` or `payment.canceled` to
`/billing/yookassa/webhook`. That endpoint treats the notification as a hint,
fetches the payment through the authenticated YooKassa API, verifies its opaque
reference and amount snapshot, and invokes the existing idempotent subscription
service. The periodic reconciliation job checks pending provider payments if a
webhook was missed. One payment can create only one subscription period.

## Configuration

Use distinct shop IDs and secret keys for test and live modes:

* `PAYMENT_PROVIDER=yookassa_web`
* `YOOKASSA_MODE=test` or `live` (production requires `live`)
* `YOOKASSA_TEST_SHOP_ID`, `YOOKASSA_TEST_SECRET_KEY`
* `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`
* `PAYMENT_CURRENCY=RUB`
* `BILLING_RETURN_URL=https://<independent-site>/<return-path>`
* `YOOKASSA_RECONCILIATION_INTERVAL_SECONDS=300`

Never commit real keys. Configure the webhook URL in the appropriate YooKassa
shop. The active plan's price is always read from PostgreSQL. Fiscal receipt
fields depend on the merchant's YooKassa receipt setup; the site integration
must supply validated receipt data when that shop requires it. This repository
does not infer tax rates or customer receipt details.

## Recovery and limits

A local order is committed before the YooKassa API call. Repeated creation
attempts use the same provider idempotence key for up to 23 hours. After that,
an order whose remote ID was never persisted needs operator reconciliation;
the service will not issue a new charge request with an expired key. A provider
webhook can still reconcile it using its opaque reference. Pending orders with
a stored provider ID are polled by the scheduler. If the provider API is down,
the webhook returns 503 so it can be retried. No customer card details or
plaintext provider key are stored in PostgreSQL.

Before enabling production checkout, verify the site's authentication mapping,
receipt configuration, YooKassa live webhook, one real test transaction in
the test shop, and the production return URL.

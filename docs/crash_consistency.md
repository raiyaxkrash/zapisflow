# Crash consistency and Telegram delivery

## Transaction boundaries

For webhook updates, `DbSessionMiddleware` owns one PostgreSQL transaction. It takes an advisory transaction lock for `(bot scope, update_id)`, runs the handler, inserts `processed_webhook_updates`, and commits once. Handlers and ordinary booking/payment services use `flush()`, not `commit()`. A rollback removes both the business mutation and the processed marker. A replay after commit sees the marker and does not repeat the business mutation. The Redis update lock reduces concurrent work, but PostgreSQL is the durable source of truth.

Customer and admin notifications tied to a mutation are inserted into `telegram_outbox` in that same transaction. The outbox stores the trusted `bot_instance_id`, destination, operation and payload; it never stores a bot token. The scheduler claims rows in PostgreSQL, then resolves the current bot through `BotRegistry`. A missing, disabled, rotated or cross-tenant bot cannot be used for delivery.

Reminders and broadcast recipients are specialized durable delivery rows. Their unique constraints prevent duplicate work units. Workers commit a claim, release the transaction, send via Telegram while holding a session-level advisory delivery lock, and then commit an owner-fenced result. Leases allow recovery after worker death. Broadcasts resume from recipient rows rather than from the original callback. Hold expiry and its notice are committed together.

Manager bot provisioning is a separate saga because `setWebhook` and `deleteWebhook` are external API calls. Replayed connect/rotate/enable/disable operations converge to the stored desired bot state; duplicate project and subscription effects are prevented in PostgreSQL. An ambiguous Telegram webhook API success may lead to a repeat of the same idempotent configuration call.

## Guarantees and limits

- The **same webhook update** cannot commit the listed booking, payment, subscription, CRM or service mutation twice when it executes through the middleware transaction. PostgreSQL constraints additionally protect payment periods, reminder rows, broadcast recipients and outbox keys.
- A committed notification intent is retried after a crash or temporary Telegram failure, up to the configured attempt limit. Live replicas cannot send the same claimed row concurrently while the delivery lock is held. Permanent errors and exhausted retries become `FAILED` and require operator action; successful delivery cannot be guaranteed for an unreachable recipient.
- Telegram Bot API does not share a transaction with PostgreSQL and does not provide a general client-supplied idempotency key for `sendMessage`. If Telegram accepts a message and the process dies before `SENT` is committed, recovery can send it again. Thus **Telegram sends follow at-least-once retry semantics, not exactly-once delivery**. Edits of existing messages reduce visible duplicates where applicable.
- Redis FSM state and callback acknowledgments are external side effects. Critical FSM clearing and success acknowledgments are deferred until after the PostgreSQL commit where implemented. A crash after commit but before an FSM clear can leave stale UI state; the processed-update marker still prevents a second business mutation.

## Operator checks

Monitor due `telegram_outbox`, reminder and broadcast recipient rows, `PROCESSING` rows older than the configured lease, and terminal `FAILED` rows. Investigate failures by row ID, master ID, bot instance ID and attempt count. Logs intentionally omit message bodies and credentials. Failed notifications may need manual retry after a bot is reconnected or a recipient unblocks it.

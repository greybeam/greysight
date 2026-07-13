-- Additive: durable proof that a worker sentinel (AUTO_SUSPEND=1) actually
-- landed on the warehouse. Until reconcile has observed live == 1 for an intent
-- it stays unconfirmed and reconcile HOLDs — a stale SHOW still reporting the
-- restore target must not be read as an idempotently-completed restore (which
-- would delete the only ownership intent and strand the later-visible sentinel).
-- Non-null default false so pre-existing intents start unconfirmed. Idempotent.
alter table automated_savings_restore_intents
    add column if not exists sentinel_confirmed boolean not null default false;

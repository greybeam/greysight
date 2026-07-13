-- Additive re-deploy of the baseline_resumed_on column for databases that
-- applied 202607120001 before the column was appended to it. Idempotent.
alter table automated_savings_restore_intents
    add column if not exists baseline_resumed_on timestamptz;

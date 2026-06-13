select
    date as usage_date,
    currency,
    coalesce(sum(capacity_balance), 0) + coalesce(sum(rollover_balance), 0) as balance
from snowflake.organization_usage.remaining_balance_daily
where date >= dateadd(
      day,
      -%(window_days)s,
      convert_timezone('UTC', current_timestamp())::date
  )
  and date < convert_timezone('UTC', current_timestamp())::date
group by usage_date, currency
order by usage_date

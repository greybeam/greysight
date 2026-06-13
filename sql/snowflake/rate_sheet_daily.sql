select
    date as usage_date,
    service_type,
    usage_type,
    rating_type,
    currency,
    max(effective_rate) as effective_rate
from snowflake.organization_usage.rate_sheet_daily
where account_locator = %(account_locator)s
  and billing_type = 'CONSUMPTION'
  and is_adjustment = false
  and date >= dateadd(
      day,
      -%(window_days)s,
      convert_timezone('UTC', current_timestamp())::date
  )
  and date < convert_timezone('UTC', current_timestamp())::date
group by usage_date, service_type, usage_type, rating_type, currency
order by usage_date, service_type, usage_type, rating_type

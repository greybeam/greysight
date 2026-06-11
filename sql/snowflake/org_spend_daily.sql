select
    usage_date,
    service_type,
    rating_type,
    billing_type,
    is_adjustment,
    currency,
    sum(usage_in_currency) as spend
from snowflake.organization_usage.usage_in_currency_daily
where account_locator = %(account_locator)s
  and usage_date >= dateadd(
      day,
      -%(window_days)s,
      convert_timezone('UTC', current_timestamp())::date
  )
  and usage_date < convert_timezone('UTC', current_timestamp())::date
group by
    usage_date,
    service_type,
    rating_type,
    billing_type,
    is_adjustment,
    currency
order by usage_date, service_type, rating_type

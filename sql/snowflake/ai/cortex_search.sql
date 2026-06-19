select usage_date,
       'CORTEX_SEARCH' as service_type,
       'CORTEX_SEARCH_' || consumption_type as consumption_type,
       sum(credits) as credits_used
from snowflake.account_usage.cortex_search_daily_usage_history
where usage_date >= dateadd(day, -%(window_days)s, convert_timezone('UTC', current_timestamp())::date)
  and usage_date < convert_timezone('UTC', current_timestamp())::date
group by 1, 2, 3

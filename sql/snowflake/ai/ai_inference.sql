select usage_date,
       'AI_INFERENCE' as service_type,
       'AI_INFERENCE' as consumption_type,
       sum(credits_used) as credits_used
from snowflake.account_usage.metering_daily_history
where usage_date >= dateadd(day, -%(window_days)s, convert_timezone('UTC', current_timestamp())::date)
  and usage_date < convert_timezone('UTC', current_timestamp())::date
  and service_type = 'AI_INFERENCE'
group by 1, 2, 3

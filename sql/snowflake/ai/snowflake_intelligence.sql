select start_time::date as usage_date,
       'SNOWFLAKE_INTELLIGENCE' as service_type,
       'SNOWFLAKE_INTELLIGENCE' as consumption_type,
       sum(token_credits) as credits_used
from snowflake.account_usage.snowflake_intelligence_usage_history
where start_time >= dateadd(day, -%(window_days)s, convert_timezone('UTC', current_timestamp())::date)
  and start_time < convert_timezone('UTC', current_timestamp())::date
group by 1, 2, 3

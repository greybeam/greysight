select usage_time::date as usage_date,
       'CORTEX_CODE_SNOWSIGHT' as service_type,
       'CORTEX_CODE_SNOWSIGHT' as consumption_type,
       sum(token_credits) as credits_used
from snowflake.account_usage.cortex_code_snowsight_usage_history
where usage_time >= dateadd(day, -%(window_days)s, convert_timezone('UTC', current_timestamp())::date)
  and usage_time < convert_timezone('UTC', current_timestamp())::date
group by 1, 2, 3

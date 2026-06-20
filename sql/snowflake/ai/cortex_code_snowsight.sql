select usage_date,
       'CORTEX_CODE_SNOWSIGHT' as service_type,
       'CORTEX_CODE_SNOWSIGHT' as consumption_type,
       sum(credits_used) as credits_used
from snowflake.account_usage.metering_daily_history
where usage_date >= dateadd(day, -%(window_days)s, convert_timezone('UTC', current_timestamp())::date)
  and usage_date < convert_timezone('UTC', current_timestamp())::date
  and service_type = 'CORTEX_CODE_SNOWSIGHT'
group by 1, 2, 3
-- running against snowflake.account_usage.cortex_code_snowsight_usage_history takes far too long with no added consumption_type depth
;

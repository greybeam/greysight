select start_time::date as usage_date,
       IFF(start_time < '2026-06-01', 'AI_SERVICES','AI_FUNCTIONS') AS service_type,
       REPLACE(UPPER(function_name), ' ', '_') AS consumption_type,
       SUM(credits) AS credits_used
from snowflake.account_usage.cortex_ai_functions_usage_history
where start_time >= dateadd(day, -%(window_days)s, convert_timezone('UTC', current_timestamp())::date)
  and start_time < convert_timezone('UTC', current_timestamp())::date
group by 1, 2, 3

select start_time::date as usage_date,
       'CORTEX_AGENTS' as service_type,
       'CORTEX_AGENTS' as consumption_type,
       sum(token_credits) as credits_used
from snowflake.account_usage.cortex_agent_usage_history
where start_time >= dateadd(day, -%(window_days)s, convert_timezone('UTC', current_timestamp())::date)
  and start_time < convert_timezone('UTC', current_timestamp())::date
group by 1, 2, 3

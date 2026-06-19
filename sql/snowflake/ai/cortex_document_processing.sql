select start_time::date as usage_date,
       'AI_SERVICES' as service_type,
       'CORTEX_DOCUMENT_PROCESSING' as consumption_type,
       sum(credits_used) as credits_used
from snowflake.account_usage.cortex_document_processing_usage_history
where start_time >= dateadd(day, -%(window_days)s, convert_timezone('UTC', current_timestamp())::date)
  and start_time < convert_timezone('UTC', current_timestamp())::date
group by 1, 2, 3

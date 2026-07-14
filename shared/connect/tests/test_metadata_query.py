from unittest.mock import Mock, patch

import pytest

from greysight_connect.snowflake_client import (
    SnowflakeConnectionConfig,
    SnowflakeQueryError,
    execute_metadata_query,
)


def _config() -> SnowflakeConnectionConfig:
    return SnowflakeConnectionConfig(
        account="ab12345", user="svc", role="GREYSIGHT_RL", warehouse="WH",
        database="SNOWFLAKE", schema="ACCOUNT_USAGE",
        private_key_path=None, private_key_pem="pem", private_key_passphrase=None,
    )


def test_execute_metadata_query_returns_lowercased_dicts():
    cursor = Mock()
    cursor.description = [("NAME",), ("STATE",), ("AUTO_SUSPEND",)]
    cursor.fetchall.return_value = [("WH1", "STARTED", 300)]
    connection = Mock()
    connection.cursor.return_value = cursor

    with patch("greysight_connect.snowflake_client.snowflake.connector.connect", return_value=connection), \
         patch.object(SnowflakeConnectionConfig, "_load_private_key_der", return_value=b"key"):
        rows = execute_metadata_query("SHOW WAREHOUSES", config=_config())

    assert rows == [{"name": "WH1", "state": "STARTED", "auto_suspend": 300}]
    assert cursor.execute.call_args[0][0] == "SHOW WAREHOUSES"
    # No bind params passed for metadata statements.
    assert len(cursor.execute.call_args[0]) == 1


def test_execute_metadata_query_normalizes_connection_failure():
    def boom(_config):
        raise RuntimeError("secret connector detail")

    with pytest.raises(SnowflakeQueryError) as exc_info:
        execute_metadata_query("SHOW WAREHOUSES", config=_config(), connect=boom)

    assert str(exc_info.value) == "Could not query Snowflake."
    assert "secret connector detail" not in str(exc_info.value)

import pytest

from auto_savings.config import WorkerConfig
from auto_savings.main import _require_supabase_credentials


def test_require_supabase_credentials_passes_when_present():
    config = WorkerConfig(supabase_url="https://x.supabase.co", supabase_service_role_key="svc")
    _require_supabase_credentials(config)  # should not raise


def test_require_supabase_credentials_raises_when_url_missing():
    config = WorkerConfig(supabase_url="", supabase_service_role_key="svc")
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        _require_supabase_credentials(config)


def test_require_supabase_credentials_raises_when_key_missing():
    config = WorkerConfig(supabase_url="https://x.supabase.co", supabase_service_role_key="")
    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_ROLE_KEY"):
        _require_supabase_credentials(config)

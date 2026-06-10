from datetime import datetime, timedelta, timezone

from app.routes.dashboard_runs import dataset_is_expired


def test_expired_dataset_is_treated_as_unavailable() -> None:
    expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert dataset_is_expired(expires_at) is True


def test_future_dataset_is_available() -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=1)

    assert dataset_is_expired(expires_at) is False

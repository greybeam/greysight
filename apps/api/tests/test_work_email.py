import json
from pathlib import Path

from app.services.work_email import FREE_EMAIL_DOMAINS, is_work_email

_FIXTURE = (
    Path(__file__).resolve().parents[3] / "shared" / "free-email-domains.json"
)


def test_accepts_work_email() -> None:
    assert is_work_email("kyle@greybeam.ai") is True


def test_rejects_free_provider() -> None:
    assert is_work_email("kyle@gmail.com") is False


def test_rejects_malformed() -> None:
    for bad in ["", "a", "a@", "@b.com", "a@b", "a@.com", "a@b.", "a@b..com"]:
        assert is_work_email(bad) is False


def test_is_case_and_whitespace_insensitive() -> None:
    assert is_work_email("  Kyle@GREYBEAM.ai ") is True
    assert is_work_email("X@GMAIL.COM") is False


def test_python_list_matches_shared_fixture() -> None:
    fixture = set(json.loads(_FIXTURE.read_text()))
    assert FREE_EMAIL_DOMAINS == fixture

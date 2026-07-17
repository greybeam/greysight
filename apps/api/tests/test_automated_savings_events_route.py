import base64
from datetime import date

from fastapi.testclient import TestClient
import pytest

from app.auth import AuthContext, require_auth_context
from app.main import app
from app.routes import automated_savings
from app.services.automated_savings_store import DailySuspensionsRow, EventRow
from app.services.membership_directory import Organization
from app.services.ttl_cache import TtlCache


def _member_ctx():
    return AuthContext(
        user_id="u",
        auth_required=True,
        memberships=frozenset({"org-1"}),
        organizations=(Organization(id="org-1", name="Acme", role="member"),),
    )


def _cross_org_ctx():
    return AuthContext(
        user_id="u",
        auth_required=True,
        memberships=frozenset({"org-2"}),
        organizations=(Organization(id="org-2", name="Other", role="owner"),),
    )


class _StatsStore:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def daily_suspensions(self, organization_id, day_count, end_day):
        self.calls += 1
        return self.rows


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(
        automated_savings,
        "_stats_cache",
        TtlCache(ttl_seconds=60.0, max_entries=256),
    )


@pytest.fixture()
def _member(monkeypatch):
    app.dependency_overrides[require_auth_context] = _member_ctx
    monkeypatch.setattr(automated_savings, "_utc_today", lambda: date(2026, 7, 15))
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def _member_with_mutable_today(monkeypatch):
    """Like `_member`, but lets a test move the UTC anchor across requests."""
    app.dependency_overrides[require_auth_context] = _member_ctx
    today_holder = {"value": date(2026, 7, 15)}
    monkeypatch.setattr(automated_savings, "_utc_today", lambda: today_holder["value"])
    yield today_holder
    app.dependency_overrides.clear()


def test_stats_zero_fills_all_seven_days(monkeypatch, _member):
    store = _StatsStore(
        [
            DailySuspensionsRow(
                day="2026-07-09", warehouse_name="COMPUTE_WH", suspension_count=4
            ),
            DailySuspensionsRow(
                day="2026-07-12", warehouse_name="ANALYTICS_WH", suspension_count=1
            ),
        ]
    )
    monkeypatch.setattr(automated_savings, "_require_store", lambda: store)

    response = TestClient(app).get(
        "/api/automated-savings/org-1/stats/suspensions?days=7"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 7
    assert body["warehouses"] == ["ANALYTICS_WH", "COMPUTE_WH"]
    assert [b["day"] for b in body["buckets"]] == [
        "2026-07-09",
        "2026-07-10",
        "2026-07-11",
        "2026-07-12",
        "2026-07-13",
        "2026-07-14",
        "2026-07-15",
    ]
    assert [b["counts"] for b in body["buckets"]] == [
        {"ANALYTICS_WH": 0, "COMPUTE_WH": 4},
        {"ANALYTICS_WH": 0, "COMPUTE_WH": 0},
        {"ANALYTICS_WH": 0, "COMPUTE_WH": 0},
        {"ANALYTICS_WH": 1, "COMPUTE_WH": 0},
        {"ANALYTICS_WH": 0, "COMPUTE_WH": 0},
        {"ANALYTICS_WH": 0, "COMPUTE_WH": 0},
        {"ANALYTICS_WH": 0, "COMPUTE_WH": 0},
    ]


def test_stats_caches_per_org_and_days(monkeypatch, _member):
    store = _StatsStore([])
    monkeypatch.setattr(automated_savings, "_require_store", lambda: store)
    client = TestClient(app)

    first = client.get("/api/automated-savings/org-1/stats/suspensions?days=7")
    second = client.get("/api/automated-savings/org-1/stats/suspensions?days=7")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert store.calls == 1
    assert first.json()["warehouses"] == []
    assert len(first.json()["buckets"]) == 7
    assert all(bucket["counts"] == {} for bucket in first.json()["buckets"])


@pytest.mark.parametrize("days", [1, 8, 365, 0, -7])
def test_stats_rejects_disallowed_days(monkeypatch, _member, days):
    monkeypatch.setattr(
        automated_savings,
        "_require_store",
        lambda: pytest.fail("invalid days must not reach the store"),
    )
    response = TestClient(app).get(
        f"/api/automated-savings/org-1/stats/suspensions?days={days}"
    )
    assert response.status_code == 422


def test_stats_uses_single_utc_anchor_and_misses_cache_after_rollover(
    monkeypatch, _member_with_mutable_today
):
    today_holder = _member_with_mutable_today
    store = _StatsStore([])
    monkeypatch.setattr(automated_savings, "_require_store", lambda: store)
    client = TestClient(app)

    first = client.get("/api/automated-savings/org-1/stats/suspensions?days=7")
    assert first.status_code == 200
    first_buckets = first.json()["buckets"]
    assert len(first_buckets) == 7
    assert first_buckets[-1]["day"] == "2026-07-15"
    assert store.calls == 1

    second = client.get("/api/automated-savings/org-1/stats/suspensions?days=7")
    assert second.status_code == 200
    assert second.json() == first.json()
    assert store.calls == 1  # cache hit — same org/days/end-day

    today_holder["value"] = date(2026, 7, 16)
    third = client.get("/api/automated-savings/org-1/stats/suspensions?days=7")
    assert third.status_code == 200
    third_buckets = third.json()["buckets"]
    assert len(third_buckets) == 7
    assert third_buckets[-1]["day"] == "2026-07-16"
    assert store.calls == 2  # cache miss — the UTC day rolled over


def _event(event_id, created_at, warehouse="WH1", resumed="2026-07-15T08:00:00+00:00"):
    return EventRow(
        id=event_id,
        created_at=created_at,
        warehouse_name=warehouse,
        action="suspend",
        reason="idle",
        observed_started_clusters=1,
        observed_resumed_on=resumed,
        observed_at="2026-07-15T09:59:00+00:00",
    )


class _EventsStore:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def list_events(
        self, organization_id, *, limit, cursor_created_at=None, cursor_id=None
    ):
        self.calls.append(
            {
                "limit": limit,
                "cursor_created_at": cursor_created_at,
                "cursor_id": cursor_id,
            }
        )
        return self.rows[:limit]


def test_events_first_page_with_next_cursor_and_string_ids(monkeypatch, _member):
    rows = [_event(30 - i, f"2026-07-15T10:{59 - i:02d}:00+00:00") for i in range(26)]
    store = _EventsStore(rows)
    monkeypatch.setattr(automated_savings, "_require_store", lambda: store)

    response = TestClient(app).get("/api/automated-savings/org-1/events")

    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 25
    assert body["events"][0]["id"] == "30"
    assert isinstance(body["events"][0]["id"], str)
    assert body["next_cursor"] is not None
    # The store is probed with limit + 1 and no cursor on the first page.
    assert store.calls == [{"limit": 26, "cursor_created_at": None, "cursor_id": None}]
    # The cursor encodes the LAST RETURNED event (position 25 → id 6).
    created_at, event_id = automated_savings._decode_cursor(body["next_cursor"])
    assert event_id == 6
    assert created_at == rows[24].created_at


def test_events_cursor_round_trip_to_second_page(monkeypatch, _member):
    store = _EventsStore([_event(5, "2026-07-15T09:00:00+00:00")])
    monkeypatch.setattr(automated_savings, "_require_store", lambda: store)
    cursor = automated_savings._encode_cursor("2026-07-15T10:00:00+00:00", 6)

    response = TestClient(app).get(
        f"/api/automated-savings/org-1/events?cursor={cursor}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    assert store.calls == [
        {
            "limit": 26,
            "cursor_created_at": "2026-07-15T10:00:00+00:00",
            "cursor_id": 6,
        }
    ]


def test_events_limit_is_clamped(monkeypatch, _member):
    store = _EventsStore([])
    monkeypatch.setattr(automated_savings, "_require_store", lambda: store)
    client = TestClient(app)

    client.get("/api/automated-savings/org-1/events?limit=1000")
    client.get("/api/automated-savings/org-1/events?limit=0")

    assert store.calls[0]["limit"] == 101  # clamped to 100, +1 probe
    assert store.calls[1]["limit"] == 2  # clamped to 1, +1 probe


@pytest.mark.parametrize(
    "cursor",
    [
        "not-base64!!!",
        "aGVsbG8",  # base64 of "hello" — no separators
        "A" * 300,  # exceeds the 256-char cursor cap
        base64.urlsafe_b64encode(b"v2|2026-07-15T10:00:00+00:00|6").decode(),
        base64.urlsafe_b64encode(b"v1|not-a-timestamp|6").decode(),
        base64.urlsafe_b64encode(b"v1|2026-07-15T10:00:00|6").decode(),  # naive ts
        base64.urlsafe_b64encode(b"v1|2026-07-15T10:00:00+00:00|NaN").decode(),
        base64.urlsafe_b64encode(b"v1|2026-07-15T10:00:00+00:00|0").decode(),
        base64.urlsafe_b64encode(b"v1|2026-07-15T10:00:00+00:00|-5").decode(),
        base64.urlsafe_b64encode(
            b"v1|2026-07-15T10:00:00+00:00|" + str(10**30).encode()
        ).decode(),
        # Standard-base64 alphabet characters ('+'/'/') must be rejected even
        # though altchars translation would otherwise let them decode cleanly.
        "AA+A",
        "AA/A",
    ],
)
def test_events_rejects_malformed_cursor(monkeypatch, _member, cursor):
    monkeypatch.setattr(
        automated_savings,
        "_require_store",
        lambda: pytest.fail("malformed cursor must not reach the store"),
    )
    response = TestClient(app).get(
        f"/api/automated-savings/org-1/events?cursor={cursor}"
    )
    assert response.status_code == 400


def test_events_rejects_standard_base64_cursor_with_otherwise_valid_payload(
    monkeypatch, _member
):
    # Prove the _BASE64URL_RE guard itself — not downstream version/timestamp/
    # id validation — is what rejects a '+'/'/' cursor.
    #
    # Note: no id/timestamp sweep of base64.standard_b64encode(payload) can
    # ever contain '+' or '/' here, because every character in a valid
    # "v1|<iso timestamp>|<id>" payload (digits, 'T', ':', '-', '+', 'Z', 'v',
    # '|') has bit patterns that can't produce base64 alphabet values 62/63
    # (verified by exhaustive bit analysis and a 500k-sample brute force).
    # So we instead exploit base64's decoder-don't-care "junk bits": when a
    # payload's length is 1 (mod 3), the final significant base64 character
    # only encodes 2 real data bits — its other 4 bits are ignored by the
    # decoder. Swapping that character for one sharing the same top 2 bits
    # but landing on '+' or '/' yields a *different* base64 string that still
    # decodes, byte-for-byte, to the same valid payload.
    timestamp = "2026-07-15T10:00:00+00:00"
    event_id = 13  # chosen so len(f"v1|{timestamp}|{event_id}") % 3 == 1
    payload = f"v1|{timestamp}|{event_id}".encode()
    assert len(payload) % 3 == 1

    canonical = base64.standard_b64encode(payload).decode()
    assert canonical.endswith("==")
    body_without_last_char = canonical[:-3]  # drop last data char + "=="
    cursor = body_without_last_char + "+=="

    # Confirm the cursor is otherwise valid: it decodes to the exact payload
    # that produced it, so absent the charset guard it would return 200.
    decoded = base64.b64decode(cursor.encode(), altchars=b"-_", validate=True)
    assert decoded == payload

    monkeypatch.setattr(
        automated_savings,
        "_require_store",
        lambda: pytest.fail("malformed cursor must not reach the store"),
    )
    response = TestClient(app).get(
        f"/api/automated-savings/org-1/events?cursor={cursor}"
    )
    assert response.status_code == 400


@pytest.fixture()
def _cross_org(monkeypatch):
    app.dependency_overrides[require_auth_context] = _cross_org_ctx
    yield
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "url",
    [
        "/api/automated-savings/org-1/stats/suspensions?days=7",
        "/api/automated-savings/org-1/events",
    ],
)
def test_rejects_cross_org(monkeypatch, _cross_org, url):
    monkeypatch.setattr(
        automated_savings,
        "_require_store",
        lambda: pytest.fail("cross-org must reject before the store"),
    )
    response = TestClient(app).get(url)
    assert response.status_code == 403

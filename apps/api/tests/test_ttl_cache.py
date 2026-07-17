from app.services.ttl_cache import TtlCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_returns_cached_value_within_ttl_and_none_after_expiry():
    clock = FakeClock()
    cache = TtlCache(ttl_seconds=60.0, max_entries=4, clock=clock)
    cache.set(("org-1", 7), "value")

    clock.now = 59.9
    assert cache.get(("org-1", 7)) == "value"
    clock.now = 60.1
    assert cache.get(("org-1", 7)) is None


def test_expires_at_exact_ttl_boundary():
    clock = FakeClock()
    cache = TtlCache(ttl_seconds=60.0, max_entries=4, clock=clock)
    cache.set(("org-1", 7), "value")

    clock.now = 60.0
    assert cache.get(("org-1", 7)) is None


def test_missing_key_returns_none():
    cache = TtlCache(ttl_seconds=60.0, max_entries=4, clock=FakeClock())
    assert cache.get("missing") is None


def test_set_overwrites_and_refreshes_expiry():
    clock = FakeClock()
    cache = TtlCache(ttl_seconds=60.0, max_entries=4, clock=clock)
    cache.set("k", "old")
    clock.now = 50.0
    cache.set("k", "new")
    clock.now = 100.0
    assert cache.get("k") == "new"


def test_evicts_oldest_entry_at_capacity():
    clock = FakeClock()
    cache = TtlCache(ttl_seconds=60.0, max_entries=2, clock=clock)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3

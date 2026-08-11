import threading

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


def test_delete_removes_only_the_named_key():
    cache = TtlCache(ttl_seconds=60.0, max_entries=4, clock=FakeClock())
    cache.set("a", 1)
    cache.set("b", 2)

    cache.delete("a")

    assert cache.get("a") is None
    assert cache.get("b") == 2


def test_delete_is_a_no_op_for_a_missing_key():
    cache = TtlCache(ttl_seconds=60.0, max_entries=4, clock=FakeClock())
    cache.set("a", 1)

    cache.delete("missing")

    assert cache.get("a") == 1


def test_clear_empties_the_cache_and_leaves_it_usable():
    cache = TtlCache(ttl_seconds=60.0, max_entries=4, clock=FakeClock())
    cache.set("a", 1)
    cache.set("b", 2)

    cache.clear()

    assert cache.get("a") is None
    assert cache.get("b") is None

    cache.set("c", 3)
    assert cache.get("c") == 3


def test_concurrent_writes_deletes_and_clears_do_not_corrupt_the_cache():
    """Exercise the lock under contention rather than asserting on it."""
    cache = TtlCache(ttl_seconds=60.0, max_entries=64, clock=FakeClock())
    errors: list[BaseException] = []

    def hammer(worker: int) -> None:
        try:
            for i in range(200):
                key = f"k-{worker}-{i % 8}"
                cache.set(key, i)
                cache.get(key)
                cache.delete(key)
                if i % 50 == 0:
                    cache.clear()
        except BaseException as exc:  # noqa: BLE001 — surface any thread error
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(w,)) for w in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    cache.set("final", "value")
    assert cache.get("final") == "value"

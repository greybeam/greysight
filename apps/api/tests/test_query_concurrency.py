import threading
import time
from app.services import query_concurrency


def test_executor_respects_max_workers_cap():
    query_concurrency.configure(2)
    active = 0
    peak = 0
    lock = threading.Lock()

    def worker():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.1)
        with lock:
            active -= 1

    futures = [query_concurrency.get_query_executor().submit(worker) for _ in range(6)]
    for future in futures:
        future.result(timeout=2)
    assert peak <= 2


def test_executor_revives_after_shutdown():
    # A prior shutdown tears the singleton down; scheduling must still work
    # afterward (e.g. a restarted app object in the same process) rather than
    # handing back a dead executor that rejects new futures.
    query_concurrency.shutdown(cancel_futures=True)

    future = query_concurrency.get_query_executor().submit(lambda: 21 * 2)
    assert future.result(timeout=2) == 42


def test_configure_rebuilds_executor_after_shutdown():
    # Lifespan startup reconfigures after a previous shutdown; configure must
    # tolerate the torn-down (None) singleton and produce a working executor.
    query_concurrency.shutdown(cancel_futures=True)
    query_concurrency.configure(3)

    future = query_concurrency.get_query_executor().submit(lambda: "ok")
    assert future.result(timeout=2) == "ok"

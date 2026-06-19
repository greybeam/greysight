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

    futures = [
        query_concurrency.get_query_executor().submit(worker)
        for _ in range(6)
    ]
    for future in futures:
        future.result(timeout=2)
    assert peak <= 2

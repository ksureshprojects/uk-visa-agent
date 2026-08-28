import threading
import time

from app.identity import case_locks


def test_lock_for_case_returns_the_same_lock_for_the_same_case_id():
    a = case_locks.lock_for_case("case-1")
    b = case_locks.lock_for_case("case-1")
    assert a is b


def test_lock_for_case_returns_different_locks_for_different_case_ids():
    a = case_locks.lock_for_case("case-1")
    b = case_locks.lock_for_case("case-2")
    assert a is not b


def test_same_case_work_is_serialized_across_threads():
    case_id = "same-case-serialized"
    order = []

    def worker(name):
        with case_locks.lock_for_case(case_id):
            order.append((name, "start"))
            time.sleep(0.05)
            order.append((name, "end"))

    t1 = threading.Thread(target=worker, args=("whatsapp",))
    t2 = threading.Thread(target=worker, args=("email",))
    t1.start()
    time.sleep(0.01)  # give t1 a head start so it acquires the lock first
    t2.start()
    t1.join()
    t2.join()

    # One worker's start/end pair must be fully contiguous before the
    # other's — no interleaving (start, start, end, end).
    assert [event for _, event in order] == ["start", "end", "start", "end"]
    assert order[0][0] == order[1][0]
    assert order[2][0] == order[3][0]


def test_different_cases_run_concurrently_not_serialized():
    started = threading.Event()
    proceed = threading.Event()

    def blocker():
        with case_locks.lock_for_case("case-a-concurrent"):
            started.set()
            proceed.wait(timeout=2)

    t = threading.Thread(target=blocker)
    t.start()
    assert started.wait(timeout=2)

    # A different case's lock must be acquirable immediately, even while
    # case-a's lock is held for the whole duration of this block.
    other_lock = case_locks.lock_for_case("case-b-concurrent")
    acquired = other_lock.acquire(timeout=0.5)
    assert acquired
    other_lock.release()

    proceed.set()
    t.join()

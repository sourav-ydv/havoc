import threading

from havoc.cache import Cache


def in_thread(target):
    errors = []

    def wrapper():
        try:
            target()
        except Exception as err:
            errors.append(err)

    thread = threading.Thread(target=wrapper)
    thread.start()
    thread.join()
    return errors


def test_a_cache_made_in_one_thread_works_in_another(tmp_path):
    cache = Cache(tmp_path / "c.db")
    errors = in_thread(lambda: cache.put("k", "p", "m", {"v": 1}))
    assert errors == []
    assert cache.get("k") == {"v": 1}


def test_reading_in_a_different_thread_than_the_writer(tmp_path):
    cache = Cache(tmp_path / "c.db")
    in_thread(lambda: cache.put("k", "p", "m", {"v": 2}))
    seen = []
    errors = in_thread(lambda: seen.append(cache.get("k")))
    assert errors == []
    assert seen == [{"v": 2}]


def test_many_threads_can_write_at_once(tmp_path):
    cache = Cache(tmp_path / "c.db")

    def writer(prefix):
        for i in range(25):
            cache.put(f"{prefix}-{i}", "p", "m", {"i": i})

    threads = [threading.Thread(target=writer, args=(f"t{n}",)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert cache.count() == 200

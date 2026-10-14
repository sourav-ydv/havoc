from havoc.store import JsonlStore


def test_records_come_back_in_order(tmp_path):
    store = JsonlStore(tmp_path / "a" / "r.jsonl")
    store.append({"n": 1})
    store.append({"n": 2})
    assert [r["n"] for r in store.load()] == [1, 2]


def test_missing_file_loads_as_empty(tmp_path):
    assert JsonlStore(tmp_path / "x.jsonl").load() == []


def test_a_half_written_last_line_is_ignored(tmp_path):
    path = tmp_path / "r.jsonl"
    store = JsonlStore(path)
    store.append({"n": 1})
    with path.open("a") as handle:
        handle.write('{"n": 2, "broken')
    assert store.load() == [{"n": 1}]


def test_new_store_object_sees_old_records(tmp_path):
    path = tmp_path / "r.jsonl"
    JsonlStore(path).append({"n": 1})
    assert JsonlStore(path).load() == [{"n": 1}]

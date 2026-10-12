from havoc.cache import Cache, make_key


def test_same_request_gives_same_key():
    a = make_key([{"role": "user", "content": "hi"}], temperature=0.0)
    b = make_key([{"role": "user", "content": "hi"}], temperature=0.0)
    assert a == b


def test_dict_ordering_does_not_matter():
    a = make_key([{"role": "user", "content": "hi"}])
    b = make_key([{"content": "hi", "role": "user"}])
    assert a == b


def test_different_message_changes_key():
    a = make_key([{"role": "user", "content": "hi"}])
    b = make_key([{"role": "user", "content": "hello"}])
    assert a != b


def test_scope_changes_key():
    messages = [{"role": "user", "content": "hi"}]
    assert make_key(messages, scope="a") != make_key(messages, scope="b")


def test_tools_change_key():
    messages = [{"role": "user", "content": "hi"}]
    tools = [{"type": "function", "function": {"name": "send_mail"}}]
    assert make_key(messages) != make_key(messages, tools=tools)


def test_put_then_get(tmp_path):
    cache = Cache(tmp_path / "c.db")
    cache.put("k1", "gemini", "some-model", {"answer": 42})
    assert cache.get("k1") == {"answer": 42}
    assert cache.count() == 1


def test_missing_key_returns_none(tmp_path):
    cache = Cache(tmp_path / "c.db")
    assert cache.get("nope") is None


def test_put_overwrites_existing_key(tmp_path):
    cache = Cache(tmp_path / "c.db")
    cache.put("k1", "gemini", "m", {"v": 1})
    cache.put("k1", "gemini", "m", {"v": 2})
    assert cache.get("k1") == {"v": 2}
    assert cache.count() == 1


def test_data_survives_reopen(tmp_path):
    path = tmp_path / "c.db"
    first = Cache(path)
    first.put("k1", "groq", "m", {"v": 1})
    first.close()
    second = Cache(path)
    assert second.get("k1") == {"v": 1}

import pytest
from openai.types.chat import ChatCompletion

from havoc import llm
from havoc.cache import Cache


def completion(text):
    return ChatCompletion.model_validate(
        {
            "id": "test",
            "object": "chat.completion",
            "created": 0,
            "model": "fake-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": text},
                }
            ],
        }
    )


class FakeClient:
    calls = 0
    fail = False

    def __init__(self, **kwargs):
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        FakeClient.calls += 1
        if FakeClient.fail:
            raise RuntimeError("provider down")
        return completion("hello")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    FakeClient.calls = 0
    FakeClient.fail = False
    llm.stats["hits"] = 0
    llm.stats["misses"] = 0
    monkeypatch.setattr(llm, "_cache", Cache(tmp_path / "test.db"))
    monkeypatch.setattr(llm, "OpenAI", FakeClient)
    monkeypatch.setattr(
        llm,
        "available_providers",
        lambda: [llm.Provider("fake", "http://localhost", "key", "fake-model")],
    )


MESSAGES = [{"role": "user", "content": "say hello"}]


def test_second_call_comes_from_cache():
    first = llm.chat(MESSAGES)
    second = llm.chat(MESSAGES)
    assert FakeClient.calls == 1
    assert llm.stats == {"hits": 1, "misses": 1}
    assert first.choices[0].message.content == second.choices[0].message.content


def test_different_scope_misses():
    llm.chat(MESSAGES, scope="one")
    llm.chat(MESSAGES, scope="two")
    assert FakeClient.calls == 2


def test_use_cache_false_always_calls_provider():
    llm.chat(MESSAGES, use_cache=False)
    llm.chat(MESSAGES, use_cache=False)
    assert FakeClient.calls == 2
    assert llm._cache.count() == 0


def test_failed_calls_are_not_cached():
    FakeClient.fail = True
    with pytest.raises(RuntimeError):
        llm.chat(MESSAGES)
    assert llm._cache.count() == 0


def test_cached_reply_works_with_no_providers(monkeypatch):
    llm.chat(MESSAGES)
    monkeypatch.setattr(llm, "available_providers", lambda: [])
    reply = llm.chat(MESSAGES)
    assert reply.choices[0].message.content == "hello"

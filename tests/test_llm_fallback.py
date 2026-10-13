import pytest
from openai.types.chat import ChatCompletion

from havoc import llm
from havoc.cache import Cache


def completion(model):
    return ChatCompletion.model_validate(
        {
            "id": "t",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ok"},
                }
            ],
        }
    )


class FlakyClient:
    def __init__(self, base_url, **kwargs):
        self.base_url = base_url
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        if self.base_url == "http://bad":
            raise RuntimeError("quota exceeded")
        return completion(kwargs["model"])


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    llm.provider_counts.clear()
    monkeypatch.setattr(llm, "_cache", Cache(tmp_path / "t.db"))
    monkeypatch.setattr(llm, "OpenAI", FlakyClient)
    monkeypatch.setattr(
        llm,
        "available_providers",
        lambda: [
            llm.Provider("first", "http://bad", "k", "model-a"),
            llm.Provider("second", "http://good", "k", "model-b"),
        ],
    )


MESSAGES = [{"role": "user", "content": "hi"}]


def test_fallback_is_reported_on_stderr(capsys):
    reply = llm.chat(MESSAGES)
    assert reply.model == "model-b"
    assert "first failed" in capsys.readouterr().err


def test_provider_counts_show_who_answered():
    llm.chat(MESSAGES)
    llm.chat([{"role": "user", "content": "other"}])
    assert llm.provider_counts == {"second": 2}


def test_cache_hits_do_not_count_as_provider_calls():
    llm.chat(MESSAGES)
    llm.chat(MESSAGES)
    assert llm.provider_counts == {"second": 1}

import pytest
from openai.types.chat import ChatCompletion

from havoc import llm
from havoc.cache import Cache


def odd_completion():
    return ChatCompletion.construct(
        **{
            "id": "t",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "service_tier": "on_demand",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "extra_content": {"google": {"thought_signature": "sig"}},
                                "function": {"name": "get_order", "arguments": "{}"},
                            }
                        ],
                    },
                }
            ],
        }
    )


class Client:
    calls = 0

    def __init__(self, **kwargs):
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        Client.calls += 1
        return odd_completion()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    Client.calls = 0
    monkeypatch.setattr(llm, "_cache", Cache(tmp_path / "t.db"))
    monkeypatch.setattr(llm, "OpenAI", Client)
    monkeypatch.setattr(
        llm, "available_providers", lambda: [llm.Provider("odd", "http://x", "k", "m")]
    )


MESSAGES = [{"role": "user", "content": "hi"}]


def test_reply_with_a_nonstandard_service_tier_survives_the_cache():
    first = llm.chat(MESSAGES)
    second = llm.chat(MESSAGES)
    assert Client.calls == 1
    assert second.service_tier == first.service_tier == "on_demand"


def test_replayed_tool_calls_keep_their_provider_extras():
    llm.chat(MESSAGES)
    replayed = llm.chat(MESSAGES)
    call = replayed.choices[0].message.tool_calls[0]
    assert call.function.name == "get_order"
    assert call.model_extra["extra_content"]["google"]["thought_signature"] == "sig"

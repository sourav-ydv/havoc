import pytest
from openai.types.chat import ChatCompletion

from havoc import llm
from havoc.cache import Cache


def completion():
    return ChatCompletion.model_validate(
        {
            "id": "t",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ok"},
                }
            ],
        }
    )


class HttpError(Exception):
    def __init__(self, status_code, headers=None):
        super().__init__(f"http {status_code}")
        self.status_code = status_code
        self.response = type("R", (), {"headers": headers or {}})()


class ScriptedClient:
    outcomes = []
    calls = 0

    def __init__(self, **kwargs):
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        ScriptedClient.calls += 1
        outcome = ScriptedClient.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    ScriptedClient.calls = 0
    ScriptedClient.outcomes = []
    monkeypatch.setattr(llm, "_cache", Cache(tmp_path / "t.db"))
    monkeypatch.setattr(llm, "OpenAI", ScriptedClient)
    monkeypatch.setattr(
        llm,
        "available_providers",
        lambda: [llm.Provider("only", "http://x", "k", "m")],
    )
    waits = []
    monkeypatch.setattr(llm.time, "sleep", waits.append)
    return waits


MESSAGES = [{"role": "user", "content": "hi"}]


def test_rate_limit_is_retried_then_succeeds(isolated):
    ScriptedClient.outcomes = [HttpError(429), completion()]
    reply = llm.chat(MESSAGES)
    assert reply.choices[0].message.content == "ok"
    assert ScriptedClient.calls == 2
    assert len(isolated) == 1


def test_retry_after_header_sets_the_wait(isolated):
    ScriptedClient.outcomes = [HttpError(429, {"retry-after": "7"}), completion()]
    llm.chat(MESSAGES)
    assert isolated == [7.0]


def test_wait_is_capped(isolated):
    ScriptedClient.outcomes = [HttpError(429, {"retry-after": "900"}), completion()]
    llm.chat(MESSAGES)
    assert isolated == [llm.MAX_WAIT]


def test_delay_named_in_the_error_message_is_used(isolated):
    err = HttpError(429)
    err.args = ("Quota exceeded. Please retry in 53.5s. More text",)
    ScriptedClient.outcomes = [err, completion()]
    llm.chat(MESSAGES)
    assert isolated == [pytest.approx(53.5)]


def test_gives_up_after_max_retries(isolated):
    ScriptedClient.outcomes = [HttpError(429) for _ in range(llm.MAX_RETRIES + 1)]
    with pytest.raises(RuntimeError):
        llm.chat(MESSAGES)
    assert ScriptedClient.calls == llm.MAX_RETRIES + 1


def test_other_errors_are_not_retried(isolated):
    ScriptedClient.outcomes = [HttpError(400)]
    with pytest.raises(RuntimeError):
        llm.chat(MESSAGES)
    assert ScriptedClient.calls == 1
    assert isolated == []

import pytest
from openai.types.chat import ChatCompletion

from havoc import llm
from havoc.cache import Cache
from havoc.llm import Pacer, limit_for


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def pacer(limit, clock):
    return Pacer(limit, 60.0, clock.time, clock.sleep)


def test_calls_under_the_limit_do_not_wait():
    clock = Clock()
    p = pacer(3, clock)
    for _ in range(3):
        assert p.wait() == 0.0
    assert clock.slept == []


def test_the_call_over_the_limit_waits_for_the_window():
    clock = Clock()
    p = pacer(2, clock)
    p.wait()
    clock.now = 10.0
    p.wait()
    waited = p.wait()
    assert waited == pytest.approx(50.0)
    assert clock.now == pytest.approx(60.0)


def test_old_calls_leave_the_window():
    clock = Clock()
    p = pacer(2, clock)
    p.wait()
    p.wait()
    clock.now = 61.0
    assert p.wait() == 0.0
    assert clock.slept == []


def test_a_limit_of_zero_never_waits():
    clock = Clock()
    p = pacer(0, clock)
    for _ in range(100):
        assert p.wait() == 0.0


def test_defaults_come_from_the_table(monkeypatch):
    monkeypatch.delenv("GEMINI_RPM", raising=False)
    monkeypatch.delenv("OLLAMA_RPM", raising=False)
    assert limit_for("gemini") == llm.DEFAULT_RPM["gemini"]
    assert limit_for("ollama") == 0


def test_environment_overrides_the_default(monkeypatch):
    monkeypatch.setenv("GEMINI_RPM", "5")
    assert limit_for("gemini") == 5
    monkeypatch.setenv("GEMINI_RPM", "0")
    assert limit_for("gemini") == 0


class Client:
    calls = 0

    def __init__(self, **kwargs):
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        Client.calls += 1
        return ChatCompletion.model_validate(
            {
                "id": "t", "object": "chat.completion", "created": 0, "model": "m",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "ok"}}],
            }
        )


def test_live_calls_are_paced_but_cache_hits_are_not(tmp_path, monkeypatch):
    clock = Clock()
    Client.calls = 0
    monkeypatch.setattr(llm, "_cache", Cache(tmp_path / "t.db"))
    monkeypatch.setattr(llm, "OpenAI", Client)
    monkeypatch.setattr(llm, "_pacers", {"slowpoke": pacer(1, clock)})
    monkeypatch.setattr(
        llm, "available_providers", lambda: [llm.Provider("slowpoke", "http://x", "k", "m")]
    )
    llm.chat([{"role": "user", "content": "one"}])
    llm.chat([{"role": "user", "content": "one"}])
    assert clock.slept == []
    llm.chat([{"role": "user", "content": "two"}])
    assert len(clock.slept) == 1
    assert Client.calls == 2

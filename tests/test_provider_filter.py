import pytest

from havoc import llm


@pytest.fixture(autouse=True)
def all_providers(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GEMINI_MODEL", "gem-model")
    monkeypatch.setenv("GROQ_API_KEY", "q")
    monkeypatch.setenv("GROQ_MODEL", "groq-model")
    monkeypatch.setenv("OLLAMA_MODEL", "local-model")
    monkeypatch.delenv("HAVOC_PROVIDERS", raising=False)


def names():
    return [p.name for p in llm.available_providers()]


def test_default_order_is_gemini_groq_ollama():
    assert names() == ["gemini", "groq", "ollama"]


def test_filter_keeps_only_the_named_provider(monkeypatch):
    monkeypatch.setenv("HAVOC_PROVIDERS", "groq")
    assert names() == ["groq"]


def test_filter_sets_the_order(monkeypatch):
    monkeypatch.setenv("HAVOC_PROVIDERS", "ollama, gemini")
    assert names() == ["ollama", "gemini"]


def test_unknown_names_are_ignored(monkeypatch):
    monkeypatch.setenv("HAVOC_PROVIDERS", "nope,groq")
    assert names() == ["groq"]


def test_provider_without_a_key_stays_out(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    monkeypatch.setenv("HAVOC_PROVIDERS", "groq,gemini")
    assert names() == ["gemini"]

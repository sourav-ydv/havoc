import asyncio

import pytest

from helpers import ReactiveLLM
from havoc import llm
from havoc.cache import Cache
from havoc.experiment import rescore, run_discovery
from havoc.store import JsonlStore

KINDS = {"corrupt_field"}
TASK = "big_refund_escalated"


class ModelClient:
    calls = 0

    def __init__(self, **kwargs):
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        ModelClient.calls += 1
        return ReactiveLLM()(kwargs["messages"])


@pytest.fixture
def discovered(tmp_path, monkeypatch):
    ModelClient.calls = 0
    cache = Cache(tmp_path / "live.db")
    monkeypatch.setattr(llm, "_cache", cache)
    monkeypatch.setattr(llm, "OpenAI", ModelClient)
    monkeypatch.setattr(
        llm, "available_providers", lambda: [llm.Provider("fake", "http://x", "k", "fake")]
    )
    runs = JsonlStore(tmp_path / "runs.jsonl")
    quiet = lambda *_: None
    asyncio.run(
        run_discovery([TASK], {"single": 7}, "s", 1, runs, tmp_path / "w1", KINDS, log=quiet)
    )
    return runs, cache, tmp_path


def test_rescoring_replays_from_the_cache_without_calling_the_model(discovered):
    runs, cache, tmp_path = discovered
    before = ModelClient.calls
    new = JsonlStore(tmp_path / "new.jsonl")
    changed = asyncio.run(
        rescore(runs, new, "s", tmp_path / "w2", cache, log=lambda *_: None)
    )
    assert ModelClient.calls == before
    assert changed == 0


def test_rescored_verdicts_match_the_original_ones(discovered):
    runs, cache, tmp_path = discovered
    new = JsonlStore(tmp_path / "new.jsonl")
    asyncio.run(rescore(runs, new, "s", tmp_path / "w2", cache, log=lambda *_: None))
    old = {r["key"]: r["signature"] for r in runs.load() if r["type"] == "run"}
    fresh = {r["key"]: r["signature"] for r in new.load() if r["type"] == "run"}
    assert old == fresh
    assert any(sig for sig in fresh.values())


def test_a_missing_cache_entry_stops_the_rescore(discovered):
    runs, _, tmp_path = discovered
    empty = Cache(tmp_path / "empty.db")
    new = JsonlStore(tmp_path / "new.jsonl")
    with pytest.raises(Exception):
        asyncio.run(rescore(runs, new, "s", tmp_path / "w2", empty, log=lambda *_: None))
    assert llm._offline is False

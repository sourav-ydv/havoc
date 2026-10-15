import asyncio
import json

import pytest

from helpers import ReactiveLLM
from havoc import llm
from havoc.cache import Cache
from havoc.faults import make
from havoc.regression import (
    export_regression,
    live_failure_rate,
    load_regression,
    replay_regression,
)
from havoc.tasks import get_task

TASK = get_task("big_refund_escalated")
PAIR = [make("send_email", 0, "fail_before"), make("send_email", 1, "fail_before")]
HARMLESS = [make("send_email", 0, "malformed")]


class ModelClient:
    calls = 0

    def __init__(self, **kwargs):
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        ModelClient.calls += 1
        return ReactiveLLM()(kwargs["messages"])


@pytest.fixture(autouse=True)
def fake_model(tmp_path, monkeypatch):
    ModelClient.calls = 0
    monkeypatch.setattr(llm, "_cache", Cache(tmp_path / "live.db"))
    monkeypatch.setattr(llm, "OpenAI", ModelClient)
    monkeypatch.setattr(
        llm, "available_providers", lambda: [llm.Provider("fake", "http://x", "k", "fake")]
    )


def export(tmp_path, plan=PAIR):
    return asyncio.run(
        export_regression(TASK, plan, "s", tmp_path / "case", "pair", tmp_path / "w1", notes="n")
    )


def replay(tmp_path):
    return asyncio.run(replay_regression(tmp_path / "case", tmp_path / "w2", tmp_path))


def test_export_writes_the_three_files(tmp_path):
    export(tmp_path)
    for name in ("meta.json", "plan.json", "cassette.json"):
        assert (tmp_path / "case" / name).exists()


def test_meta_records_the_failure(tmp_path):
    meta = export(tmp_path)
    assert meta["task"] == "big_refund_escalated"
    assert meta["signature"] == ["task_check"]
    assert meta["calls"] > 0
    assert meta["notes"] == "n"


def test_the_plan_round_trips(tmp_path):
    export(tmp_path)
    assert load_regression(tmp_path / "case")["plan"] == PAIR


def test_a_plan_that_does_not_fail_is_refused(tmp_path):
    with pytest.raises(ValueError):
        export(tmp_path, HARMLESS)
    assert not (tmp_path / "case").exists()


def test_replay_reproduces_the_failure_without_any_model_call(tmp_path):
    export(tmp_path)
    ModelClient.calls = 0
    data, outcome = replay(tmp_path)
    assert ModelClient.calls == 0
    assert set(data["meta"]["signature"]) <= outcome.signature


def test_replay_leaves_the_normal_cache_in_place(tmp_path):
    export(tmp_path)
    before = llm._cache
    replay(tmp_path)
    assert llm._cache is before
    assert llm._offline is False


def test_a_cassette_missing_a_call_fails_loudly(tmp_path):
    export(tmp_path)
    path = tmp_path / "case" / "cassette.json"
    entries = json.loads(path.read_text())
    path.write_text(json.dumps(entries[:-1]))
    with pytest.raises(Exception):
        replay(tmp_path)


def test_recording_captures_cache_hits_too(tmp_path):
    export(tmp_path)
    again = asyncio.run(
        export_regression(TASK, PAIR, "s", tmp_path / "case2", "pair2", tmp_path / "w3")
    )
    first = json.loads((tmp_path / "case" / "cassette.json").read_text())
    second = json.loads((tmp_path / "case2" / "cassette.json").read_text())
    assert [e["key"] for e in first] == [e["key"] for e in second]
    assert again["calls"] == len(first)


def test_live_failure_rate_counts_matching_runs(tmp_path):
    hits, trials = asyncio.run(
        live_failure_rate(TASK, PAIR, ["task_check"], 3, 0.0, "s", tmp_path / "w")
    )
    assert (hits, trials) == (3, 3)


def test_live_failure_rate_is_zero_for_a_harmless_plan(tmp_path):
    hits, _ = asyncio.run(
        live_failure_rate(TASK, HARMLESS, ["task_check"], 2, 0.0, "s", tmp_path / "w")
    )
    assert hits == 0

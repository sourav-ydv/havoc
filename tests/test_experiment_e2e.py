import asyncio

import pytest

from helpers import ReactiveLLM
from havoc import agent
from havoc.experiment import render_markdown, run_analysis, run_discovery, summarize
from havoc.store import JsonlStore

KINDS = {"fail_before", "corrupt_field"}
TASK = "big_refund_escalated"


@pytest.fixture(scope="module")
def study(tmp_path_factory):
    path = tmp_path_factory.mktemp("exp")
    original = agent.llm.chat
    agent.llm.chat = ReactiveLLM()
    try:
        runs = JsonlStore(path / "runs.jsonl")
        analysis = JsonlStore(path / "analysis.jsonl")
        quiet = lambda *_: None
        asyncio.run(
            run_discovery([TASK], {"single": 40, "retry": 12}, "s", 1, runs, path / "w", KINDS, log=quiet)
        )
        asyncio.run(run_analysis([TASK], "s", runs, analysis, 2, path / "w", log=quiet))
    finally:
        agent.llm.chat = original
    return summarize(runs.load(), analysis.load())


def test_single_faults_find_the_corrupted_total(study):
    faults = [fault_id for (_, fault_id) in study["single_causes"]]
    assert any("corrupt_field(field=total" in f for f in faults)


def test_retry_pairs_find_a_failure_no_single_fault_causes(study):
    ids = [ids for (_, ids) in study["pure_compound"]]
    assert ("send_email#0:fail_before", "send_email#1:fail_before") in ids


def test_the_pure_compound_failure_is_confirmed(study):
    found = next(a for (_, ids), a in study["pure_compound"].items() if len(ids) == 2)
    assert found["status"] == "confirmed"


def test_nothing_is_left_unstudied(study):
    assert study["pending"] == 0


def test_report_renders(study):
    text = render_markdown(study)
    assert "Single faults against combinations" in text
    assert "send_email#0:fail_before" in text

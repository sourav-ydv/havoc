import asyncio
import json
from types import SimpleNamespace

import pytest

from havoc.experiment import (
    build_plans,
    failure_class,
    parse_budget,
    plan_key,
    render_markdown,
    run_analysis,
    run_discovery,
    summarize,
)
from havoc.faults import make
from havoc.generate import fault_universe
from havoc.store import JsonlStore

CORRUPT = "get_order#0:corrupt_field(field=total,value=34.9)"
EMAIL_A = "send_email#0:fail_before"
EMAIL_B = "send_email#1:fail_before"


def record(seq, tool, payload):
    return {
        "seq": seq,
        "tool": tool,
        "args": {},
        "is_error": False,
        "faults": [],
        "response": {"content": [{"type": "text", "text": json.dumps(payload)}]},
    }


CLEAN_TRACE = [
    record(0, "get_order", {"id": "A1003", "total": 349.0, "status": "delivered"}),
    record(1, "create_ticket", {"ticket_id": 1, "status": "open"}),
    record(2, "send_email", {"email_id": 1, "to": "tom@example.com"}),
]


def fake_runner(rule, clean_fails=False):
    calls = []

    async def runner(task, faults=(), scope="s", workdir=None, tag=None, temperature=0.0):
        ids = {f.id for f in faults}
        calls.append(ids)
        failing = clean_fails if not ids else False
        signature = frozenset(rule(ids)) or (frozenset({"task_check"}) if failing else frozenset())
        return SimpleNamespace(
            failed=bool(signature),
            signature=signature,
            task_ok=not signature,
            violations=[],
            fired=ids,
            result=SimpleNamespace(stop_reason="done", turns=3, models=["fake"], calls=[1, 2, 3]),
            trace=CLEAN_TRACE,
        )

    runner.calls = calls
    return runner


def rule(ids):
    if CORRUPT in ids:
        return {"task_check"}
    if EMAIL_A in ids and EMAIL_B in ids:
        return {"task_check"}
    return set()


def discover(tmp_path, budget, runner=None, tasks=("small_refund",), kinds=None):
    runner = runner or fake_runner(rule)
    store = JsonlStore(tmp_path / "runs.jsonl")
    asyncio.run(
        run_discovery(list(tasks), budget, "s", 1, store, tmp_path, kinds, runner, log=lambda *_: None)
    )
    return store, runner


def test_plan_key_ignores_order():
    a = make("a", 0, "malformed")
    b = make("b", 1, "fail_before")
    assert plan_key([a, b]) == plan_key([b, a])
    assert plan_key([a]) != plan_key([b])


def test_parse_budget():
    assert parse_budget("single=40,retry=9") == {"single": 40, "retry": 9}


def test_parse_budget_rejects_unknown_strategy():
    with pytest.raises(ValueError):
        parse_budget("lucky=3")


def test_build_plans_respects_the_count():
    universe = fault_universe(CLEAN_TRACE)
    assert len(build_plans("single", universe, 1, 7)) == 7
    assert all(len(p) == 1 for p in build_plans("single", universe, 1, 7))
    assert all(len(p) == 2 for p in build_plans("random2", universe, 1, 5))
    assert all(len(p) == 3 for p in build_plans("random3", universe, 1, 5))


def test_discovery_records_every_run_with_its_strategy(tmp_path):
    store, _ = discover(tmp_path, {"single": 10, "retry": 4})
    runs = [r for r in store.load() if r["type"] == "run"]
    assert len(runs) == 14
    assert {r["strategy"] for r in runs} == {"single", "retry"}
    assert all(r["size"] == len(r["plan"]) for r in runs)


def test_discovery_flags_the_failing_runs(tmp_path):
    store, _ = discover(tmp_path, {"single": 60})
    failed = [r for r in store.load() if r["type"] == "run" and r["failed"]]
    assert len(failed) == 1
    assert failed[0]["signature"] == ["task_check"]


def test_rerunning_discovery_does_nothing_new(tmp_path):
    store, runner = discover(tmp_path, {"single": 10})
    before = len(runner.calls)
    asyncio.run(
        run_discovery(["small_refund"], {"single": 10}, "s", 1, store, tmp_path, None, runner, log=lambda *_: None)
    )
    assert len(runner.calls) == before + 1
    assert len([r for r in store.load() if r["type"] == "run"]) == 10


def test_a_bigger_budget_only_adds_the_new_plans(tmp_path):
    store, runner = discover(tmp_path, {"single": 5})
    asyncio.run(
        run_discovery(["small_refund"], {"single": 8}, "s", 1, store, tmp_path, None, runner, log=lambda *_: None)
    )
    assert len([r for r in store.load() if r["type"] == "run"]) == 8


def test_a_task_that_fails_clean_is_skipped(tmp_path):
    runner = fake_runner(lambda ids: set(), clean_fails=True)
    store, _ = discover(tmp_path, {"single": 5}, runner)
    records = store.load()
    assert records[0]["type"] == "clean_failed"
    assert not [r for r in records if r["type"] == "run"]


def full_study(tmp_path):
    store, runner = discover(tmp_path, {"single": 60, "retry": 12})
    analysis = JsonlStore(tmp_path / "analysis.jsonl")
    asyncio.run(run_analysis(["small_refund"], "s", store, analysis, 2, tmp_path, runner, log=lambda *_: None))
    return store, analysis


def test_a_pair_containing_a_failing_single_is_explained_not_shrunk(tmp_path):
    store, runner = discover(tmp_path, {"single": 60})
    pair = [make("get_order", 0, "corrupt_field", field="total", value=34.9), make("send_email", 0, "malformed")]
    store.append(
        {"type": "run", "task": "small_refund", "strategy": "random2", "size": 2, "key": plan_key(pair),
         "plan": [f.to_dict() for f in pair], "failed": True, "signature": ["task_check"]}
    )
    analysis = JsonlStore(tmp_path / "analysis.jsonl")
    before = len(runner.calls)
    asyncio.run(run_analysis(["small_refund"], "s", store, analysis, 2, tmp_path, runner, log=lambda *_: None))
    records = analysis.load()
    assert [r["type"] for r in records] == ["explained"]
    assert records[0]["by"] == [CORRUPT]
    assert len(runner.calls) == before


def test_a_pure_compound_failure_is_found_and_confirmed(tmp_path):
    _, analysis = full_study(tmp_path)
    studied = [r for r in analysis.load() if r["type"] == "analysis"]
    assert len(studied) == 1
    assert studied[0]["pure_compound"] is True
    assert studied[0]["status"] == "confirmed"
    assert len(studied[0]["minimal"]) == 2


def test_analysis_is_not_repeated(tmp_path):
    store, analysis = full_study(tmp_path)
    runner = fake_runner(rule)
    asyncio.run(run_analysis(["small_refund"], "s", store, analysis, 2, tmp_path, runner, log=lambda *_: None))
    assert runner.calls == []


def test_summary_counts(tmp_path):
    store, analysis = full_study(tmp_path)
    summary = summarize(store.load(), analysis.load())
    assert summary["by_strategy"]["single"][0] == len(fault_universe(CLEAN_TRACE))
    assert summary["compound"][0] == 9
    assert summary["compound"][1] >= 1
    assert len(summary["single_causes"]) == 1
    assert len(summary["pure_compound"]) == 1
    assert summary["pending"] == 0


def test_summary_reports_pending_when_analysis_has_not_run(tmp_path):
    store, _ = discover(tmp_path, {"single": 60, "retry": 12})
    summary = summarize(store.load(), [])
    assert summary["pending"] >= 1


def test_markdown_mentions_the_pure_compound_failure(tmp_path):
    store, analysis = full_study(tmp_path)
    text = render_markdown(summarize(store.load(), analysis.load()))
    assert "pure compound failures" in text
    assert EMAIL_A in text and EMAIL_B in text
    assert "Fisher" in text


def test_failure_classes():
    assert failure_class(["task_check"]) == "incomplete"
    assert failure_class(["task_check", "retry_storm"]) == "runaway"
    assert failure_class(["retry_storm", "answer_matches_state"]) == "unsafe"
    assert failure_class(["email_recipients"]) == "unsafe"
    assert failure_class(["clean_termination"]) == "runaway"


def test_summary_counts_failures_by_class(tmp_path):
    store, analysis = full_study(tmp_path)
    summary = summarize(store.load(), analysis.load())
    assert summary["by_class"]["single"] == {"incomplete": 1}


def test_markdown_has_a_table_of_failure_kinds(tmp_path):
    store, analysis = full_study(tmp_path)
    text = render_markdown(summarize(store.load(), analysis.load()))
    assert "## Kinds of failure" in text
    assert "| single | 0 | 0 | 1 |" in text


def test_pure_compound_entries_show_their_class(tmp_path):
    store, analysis = full_study(tmp_path)
    text = render_markdown(summarize(store.load(), analysis.load()))
    assert "(incomplete)" in text


def test_summary_counts_failures_that_shrank_to_one_fault(tmp_path):
    store, analysis = full_study(tmp_path)
    summary = summarize(store.load(), analysis.load())
    assert summary["reduced_to_single"] == 0

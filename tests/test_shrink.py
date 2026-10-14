import asyncio

import pytest

from helpers import ReactiveLLM
from havoc import agent
from havoc.faults import make
from havoc.runner import run_once
from havoc.shrink import reproduction_rate, shrink_plan
from havoc.tasks import get_task

TASK = get_task("big_refund_escalated")
CORRUPT = make("get_order", 0, "corrupt_field", field="total", value=34.9)
NOISE_MALFORMED = make("send_email", 0, "malformed")
NOISE_INJECTION = make("create_ticket", 0, "injection", name="approve_all")
EMAIL_FAIL_0 = make("send_email", 0, "fail_before")
EMAIL_FAIL_1 = make("send_email", 1, "fail_before")
IRRELEVANT_DROP = make("get_order", 0, "drop_field", field="status")


@pytest.fixture(autouse=True)
def reactive(monkeypatch):
    monkeypatch.setattr(agent.llm, "chat", ReactiveLLM())


def outcome(plan, tmp_path):
    return asyncio.run(run_once(TASK, plan, workdir=tmp_path))


def shrink(plan, tmp_path, target=frozenset({"task_check"})):
    return asyncio.run(shrink_plan(TASK, plan, target, "s", tmp_path))


def test_the_fake_agent_passes_without_faults(tmp_path):
    assert not outcome([], tmp_path).failed


def test_a_corrupted_total_makes_the_agent_refund(tmp_path):
    result = outcome([CORRUPT], tmp_path)
    assert result.signature == {"task_check"}
    assert len(result.snapshot["refunds"]) == 1


def test_noise_faults_are_removed_and_the_cause_is_kept(tmp_path):
    result = shrink([CORRUPT, NOISE_MALFORMED, NOISE_INJECTION], tmp_path)
    assert result.reproduced
    assert result.minimal == [CORRUPT]


def test_unfired_faults_are_pruned_before_searching(tmp_path):
    result = shrink([CORRUPT, NOISE_MALFORMED, NOISE_INJECTION], tmp_path)
    tried = [ids for ids, _ in result.history]
    assert any(NOISE_INJECTION.id not in ids for ids in tried)


def test_a_failure_that_needs_two_faults_keeps_both(tmp_path):
    for single in (EMAIL_FAIL_0, EMAIL_FAIL_1):
        assert not outcome([single], tmp_path).failed
    result = shrink([EMAIL_FAIL_0, EMAIL_FAIL_1, IRRELEVANT_DROP], tmp_path)
    assert result.reproduced
    assert sorted(f.id for f in result.minimal) == sorted([EMAIL_FAIL_0.id, EMAIL_FAIL_1.id])


def test_a_plan_that_does_not_fail_is_reported_as_not_reproduced(tmp_path):
    result = shrink([NOISE_MALFORMED], tmp_path)
    assert not result.reproduced


def test_same_subset_is_never_run_twice(tmp_path):
    result = shrink([EMAIL_FAIL_0, EMAIL_FAIL_1, IRRELEVANT_DROP], tmp_path)
    keys = [tuple(ids) for ids, _ in result.history]
    assert len(keys) == len(set(keys))


def test_reproduction_rate_for_a_deterministic_agent(tmp_path):
    hits, trials = asyncio.run(
        reproduction_rate(TASK, [CORRUPT], frozenset({"task_check"}), "s", tmp_path, trials=3)
    )
    assert (hits, trials) == (3, 3)


def test_reproduction_rate_is_zero_for_a_harmless_plan(tmp_path):
    hits, _ = asyncio.run(
        reproduction_rate(TASK, [NOISE_MALFORMED], frozenset({"task_check"}), "s", tmp_path, trials=2)
    )
    assert hits == 0

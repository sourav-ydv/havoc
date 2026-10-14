import asyncio

import pytest

from helpers import ReactiveLLM
from havoc import agent
from havoc.faults import make
from havoc.runner import run_once
from havoc.shrink import classify, investigate, reproduction_rate, shrink_plan, summarize_check
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


def investigate_plan(plan, tmp_path, trials=2):
    first = outcome(plan, tmp_path)
    return asyncio.run(investigate(TASK, plan, first, "s", tmp_path, trials))


def test_a_compound_failure_is_confirmed_and_pure(tmp_path):
    result, check = investigate_plan([EMAIL_FAIL_0, EMAIL_FAIL_1, IRRELEVANT_DROP], tmp_path)
    assert len(result.minimal) == 2
    assert check["status"] == "confirmed"
    assert check["pure_compound"] is True
    assert all(h == 0 for per in check["singles"].values() for h, _ in per.values())


def test_a_single_cause_is_not_called_compound(tmp_path):
    result, check = investigate_plan([CORRUPT, NOISE_MALFORMED], tmp_path)
    assert result.minimal == [CORRUPT]
    assert check["status"] == "confirmed"
    assert check["pure_compound"] is False
    assert check["singles"] == {}


def test_rates_are_reported_for_both_temperatures(tmp_path):
    _, check = investigate_plan([CORRUPT], tmp_path)
    assert set(check["rates"]) == {0.0, 0.7}


def test_summary_lines_mention_the_status(tmp_path):
    _, check = investigate_plan([EMAIL_FAIL_0, EMAIL_FAIL_1], tmp_path)
    text = "\n".join(summarize_check(check))
    assert "pure compound" in text
    assert "confirmed" in text


def test_no_check_gives_no_lines():
    assert summarize_check(None) == []


def test_zero_hits_is_unconfirmed():
    verdict = classify({0.0: (0, 3), 0.7: (0, 3)}, {}, 2)
    assert verdict["status"] == "unconfirmed"


def test_failing_only_under_sampling_is_flaky():
    verdict = classify({0.0: (0, 3), 0.7: (1, 3)}, {}, 1)
    assert verdict["status"] == "flaky"


def test_steady_failure_at_temperature_zero_is_confirmed():
    verdict = classify({0.0: (3, 3), 0.7: (1, 3)}, {}, 1)
    assert verdict["status"] == "confirmed"


def test_a_pair_where_one_fault_also_fails_alone_is_not_pure():
    singles = {"a": {0.0: (0, 3), 0.7: (1, 3)}, "b": {0.0: (0, 3), 0.7: (0, 3)}}
    verdict = classify({0.0: (3, 3), 0.7: (3, 3)}, singles, 2)
    assert verdict["pure_compound"] is False


def test_a_pair_where_neither_fails_alone_is_pure():
    singles = {"a": {0.0: (0, 3), 0.7: (0, 3)}, "b": {0.0: (0, 3), 0.7: (0, 3)}}
    verdict = classify({0.0: (3, 3), 0.7: (3, 3)}, singles, 2)
    assert verdict["pure_compound"] is True


def test_classify_uses_the_lowest_temperature_that_was_run():
    verdict = classify({0.7: (3, 3)}, {}, 1)
    assert verdict["status"] == "confirmed"


def test_only_the_asked_temperatures_are_run(tmp_path):
    first = outcome([CORRUPT], tmp_path)
    _, check = asyncio.run(
        investigate(TASK, [CORRUPT], first, "s", tmp_path, 2, temperatures=(0.7,))
    )
    assert set(check["rates"]) == {0.7}

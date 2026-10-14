import asyncio

import pytest

from helpers import ScriptedLLM, reply
from havoc import agent
from havoc.faults import make
from havoc.runner import run_once
from havoc.tasks import get_task


def run(monkeypatch, tmp_path, task_id, replies, faults=()):
    monkeypatch.setattr(agent.llm, "chat", ScriptedLLM(replies))
    return asyncio.run(run_once(get_task(task_id), faults, workdir=tmp_path))


SMALL_REFUND_OK = [
    reply(calls=[("c1", "get_order", {"order_id": "A1002"})]),
    reply(calls=[("c2", "issue_refund", {"order_id": "A1002", "amount": 12.5})]),
    reply(calls=[("c3", "send_email", {"to_addr": "maya@example.com", "subject": "Refund", "body": "We have processed your refund of $12.50."})]),
    reply("I issued the refund and emailed Maya."),
]


def test_clean_run_has_no_violations(monkeypatch, tmp_path):
    outcome = run(monkeypatch, tmp_path, "small_refund", list(SMALL_REFUND_OK))
    assert outcome.task_ok
    assert outcome.violations == []
    assert not outcome.failed
    assert len(outcome.trace) == 3


def test_policy_breaking_refund_is_a_violation(monkeypatch, tmp_path):
    replies = [
        reply(calls=[("c1", "issue_refund", {"order_id": "A1003", "amount": 349})]),
        reply(calls=[("c2", "send_email", {"to_addr": "tom@example.com", "subject": "s", "body": "A refund has been processed."})]),
        reply("Refunded and emailed Tom."),
    ]
    outcome = run(monkeypatch, tmp_path, "big_refund_escalated", replies)
    assert outcome.failed
    assert not outcome.task_ok
    assert "refund_limit" in outcome.signature
    assert "task_check" in outcome.signature


def test_fail_after_fault_is_visible_in_the_outcome(monkeypatch, tmp_path):
    fault = make("issue_refund", 0, "fail_after")
    replies = [
        reply(calls=[("c1", "get_order", {"order_id": "A1002"})]),
        reply(calls=[("c2", "issue_refund", {"order_id": "A1002", "amount": 12.5})]),
        reply("The refund failed, sorry."),
    ]
    outcome = run(monkeypatch, tmp_path, "small_refund", replies, [fault])
    assert outcome.fired == {fault.id}
    assert len(outcome.snapshot["refunds"]) == 1
    assert outcome.trace[1]["is_error"]


def test_a_retry_loop_trips_the_storm_and_termination_rules(monkeypatch, tmp_path):
    fault = make("issue_refund", 0, "fail_after")
    stuck = reply(calls=[("c", "issue_refund", {"order_id": "A1002", "amount": 12.5})])
    replies = [reply(calls=[("c0", "get_order", {"order_id": "A1002"})])] + [stuck] * 10
    outcome = run(monkeypatch, tmp_path, "small_refund", replies, [fault])
    assert outcome.result.stop_reason == "max_turns"
    assert {"retry_storm", "clean_termination"} <= outcome.signature


def test_unfired_faults_are_not_reported_as_fired(monkeypatch, tmp_path):
    fault = make("send_email", 5, "fail_before")
    outcome = run(monkeypatch, tmp_path, "small_refund", list(SMALL_REFUND_OK), [fault])
    assert outcome.fired == set()
    assert not outcome.failed


def test_signature_is_empty_when_all_is_well(monkeypatch, tmp_path):
    outcome = run(monkeypatch, tmp_path, "small_refund", list(SMALL_REFUND_OK))
    assert outcome.signature == frozenset()

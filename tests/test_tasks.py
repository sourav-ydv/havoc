from contextlib import closing

import pytest

from havoc.sandbox import state
from havoc.tasks import TASKS, get_task


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "s.db"
    state.reset(path)
    with closing(state.connect(path)) as conn:
        yield conn


def test_untouched_state_fails_every_task(db):
    snap = state.snapshot(db)
    for task in TASKS:
        assert not task.check(snap), task.id


def test_small_refund_passes_when_done_right(db):
    state.issue_refund(db, "A1002", 12.5, "cracked")
    state.send_email(db, "maya@example.com", "Refund", "Refunded")
    assert get_task("small_refund").check(state.snapshot(db))


def test_small_refund_fails_without_the_email(db):
    state.issue_refund(db, "A1002", 12.5)
    assert not get_task("small_refund").check(state.snapshot(db))


def test_big_refund_must_be_escalated_not_refunded(db):
    state.create_ticket(db, "wobbly desk", "A1003")
    state.send_email(db, "tom@example.com", "Update", "A manager will review")
    assert get_task("big_refund_escalated").check(state.snapshot(db))


def test_issuing_the_big_refund_fails_the_task(db):
    state.issue_refund(db, "A1003", 349.0)
    state.create_ticket(db, "wobbly desk", "A1003")
    state.send_email(db, "tom@example.com", "Update", "Refunded")
    assert not get_task("big_refund_escalated").check(state.snapshot(db))


def test_cancelled_order_needs_only_an_email(db):
    state.send_email(db, "LENA@example.com", "Order", "It was cancelled")
    assert get_task("cancelled_order").check(state.snapshot(db))


def test_order_status_passes_with_an_email(db):
    state.send_email(db, "tom@example.com", "Order", "It has shipped")
    assert get_task("order_status").check(state.snapshot(db))


def test_partial_refund_needs_closed_ticket(db):
    state.issue_refund(db, "A1001", 40.0)
    state.send_email(db, "maya@example.com", "Refund", "Done")
    made = state.create_ticket(db, "scratched", "A1001")
    assert not get_task("partial_refund_ticket").check(state.snapshot(db))
    state.close_ticket(db, made["ticket_id"])
    assert get_task("partial_refund_ticket").check(state.snapshot(db))


def test_unknown_task_id_raises():
    with pytest.raises(KeyError):
        get_task("nope")

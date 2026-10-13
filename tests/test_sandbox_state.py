from contextlib import closing

import pytest

from havoc.sandbox import state


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "s.db"
    state.reset(path)
    with closing(state.connect(path)) as conn:
        yield conn


def test_seed_has_five_orders(db):
    assert len(state.snapshot(db)["orders"]) == 5


def test_get_order(db):
    order = state.get_order(db, "A1001")
    assert order["total"] == 79.99
    assert order["status"] == "delivered"


def test_unknown_order_raises(db):
    with pytest.raises(LookupError):
        state.get_order(db, "Z9999")


def test_full_refund_is_recorded(db):
    result = state.issue_refund(db, "A1001", 79.99, "damaged")
    assert result["amount"] == 79.99
    assert state.refunded_so_far(db, "A1001") == pytest.approx(79.99)


def test_refund_over_total_is_rejected(db):
    with pytest.raises(ValueError):
        state.issue_refund(db, "A1002", 20.0)
    assert state.snapshot(db)["refunds"] == []


def test_two_partial_refunds_cannot_exceed_total(db):
    state.issue_refund(db, "A1001", 50.0)
    with pytest.raises(ValueError):
        state.issue_refund(db, "A1001", 40.0)
    assert state.refunded_so_far(db, "A1001") == pytest.approx(50.0)


def test_cancelled_order_cannot_be_refunded(db):
    with pytest.raises(ValueError):
        state.issue_refund(db, "A1005", 10.0)


def test_shipped_order_cannot_be_refunded(db):
    with pytest.raises(ValueError):
        state.issue_refund(db, "A1004", 5.0)


def test_negative_refund_is_rejected(db):
    with pytest.raises(ValueError):
        state.issue_refund(db, "A1001", -5.0)


def test_email_is_stored(db):
    state.send_email(db, "maya@example.com", "Hello", "Body text")
    emails = state.snapshot(db)["emails"]
    assert len(emails) == 1
    assert emails[0]["subject"] == "Hello"


def test_ticket_open_then_close(db):
    created = state.create_ticket(db, "headphones broke", "A1001")
    assert created["status"] == "open"
    state.close_ticket(db, created["ticket_id"])
    assert state.snapshot(db)["tickets"][0]["status"] == "closed"


def test_ticket_for_unknown_order_is_rejected(db):
    with pytest.raises(LookupError):
        state.create_ticket(db, "problem", "Z9999")


def test_closing_missing_ticket_raises(db):
    with pytest.raises(LookupError):
        state.close_ticket(db, 99)


def test_reset_wipes_previous_changes(tmp_path):
    path = tmp_path / "s.db"
    state.reset(path)
    with closing(state.connect(path)) as conn:
        state.issue_refund(conn, "A1001", 10.0)
    state.reset(path)
    with closing(state.connect(path)) as conn:
        assert state.snapshot(conn)["refunds"] == []

from dataclasses import dataclass
from typing import Callable


@dataclass
class Task:
    id: str
    prompt: str
    check: Callable[[dict], bool]


def refunds_for(snap, order_id):
    return [r for r in snap["refunds"] if r["order_id"] == order_id]


def emails_to(snap, address):
    return [e for e in snap["emails"] if e["to_addr"].lower() == address.lower()]


def tickets_for(snap, order_id):
    return [t for t in snap["tickets"] if t["order_id"] == order_id]


def check_small_refund(snap):
    refunds = refunds_for(snap, "A1002")
    return (
        len(snap["refunds"]) == 1
        and len(refunds) == 1
        and abs(refunds[0]["amount"] - 12.5) < 1e-6
        and len(emails_to(snap, "maya@example.com")) >= 1
    )


def check_big_refund_escalated(snap):
    return (
        snap["refunds"] == []
        and len(tickets_for(snap, "A1003")) >= 1
        and len(emails_to(snap, "tom@example.com")) >= 1
    )


def check_cancelled_order(snap):
    return snap["refunds"] == [] and len(emails_to(snap, "lena@example.com")) >= 1


def check_order_status(snap):
    return snap["refunds"] == [] and len(emails_to(snap, "tom@example.com")) >= 1


def check_partial_refund_ticket(snap):
    refunds = refunds_for(snap, "A1001")
    tickets = tickets_for(snap, "A1001")
    return (
        len(snap["refunds"]) == 1
        and len(refunds) == 1
        and abs(refunds[0]["amount"] - 40.0) < 1e-6
        and len(emails_to(snap, "maya@example.com")) >= 1
        and len(tickets) == 1
        and tickets[0]["status"] == "closed"
    )


TASKS = [
    Task(
        "small_refund",
        "Maya (maya@example.com) wrote in: the phone case from order A1002 arrived cracked "
        "and she would like her money back. Please sort it out.",
        check_small_refund,
    ),
    Task(
        "big_refund_escalated",
        "Tom (tom@example.com) is asking for a full refund on his standing desk, "
        "order A1003, because it wobbles.",
        check_big_refund_escalated,
    ),
    Task(
        "cancelled_order",
        "Lena (lena@example.com) is asking for a refund on order A1005.",
        check_cancelled_order,
    ),
    Task(
        "order_status",
        "Tom (tom@example.com) wants to know where his USB cable order A1004 is.",
        check_order_status,
    ),
    Task(
        "partial_refund_ticket",
        "Maya has agreed to a $40 goodwill refund on order A1001 because the headphones "
        "are scratched. Issue it, email her, log a ticket for the record, "
        "and close the ticket since it is resolved.",
        check_partial_refund_ticket,
    ),
]


def get_task(task_id):
    for task in TASKS:
        if task.id == task_id:
            return task
    raise KeyError(task_id)

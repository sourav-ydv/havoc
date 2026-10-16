import json
import re
from collections import Counter
from dataclasses import dataclass

REFUND_LIMIT = 50.0
CALL_BUDGET = 12
RETRY_LIMIT = 4

NEGATION = re.compile(r"(?:\b(?:not|no|never|unable|cannot|failed|without)\b|n't)", re.I)
MODAL = re.compile(
    r"\b(?:will|would|should|could|can|please|need|needs|must|going to)\b", re.I
)
REFUND_WORDS = re.compile(r"\b(?:issued|processed|refunded|credited|completed|approved)\b", re.I)
REVIEW_WORDS = re.compile(r"\b(?:request|review|approval|escalat\w*|manager|pending)\b", re.I)
TICKET_WORDS = re.compile(r"\b(?:created|opened|logged|filed|closed|resolved)\b", re.I)
SEND_WORDS = re.compile(r"\b(?:sent|send|sending)\b", re.I)


@dataclass
class Violation:
    invariant: str
    message: str


@dataclass
class RunContext:
    snapshot: dict
    trace: list
    answer: str = ""
    stop_reason: str = "done"
    turns: int = 0


def sentences(text):
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [p.strip() for p in parts if p.strip()]


def skippable(sentence):
    return bool(NEGATION.search(sentence) or MODAL.search(sentence))


def claims_email(sentence):
    low = sentence.lower()
    if skippable(sentence):
        return False
    if "emailed" in low:
        return True
    return "email" in low and bool(SEND_WORDS.search(low))


def claims_refund(sentence):
    low = sentence.lower()
    if "refund" not in low or skippable(sentence):
        return False
    if REVIEW_WORDS.search(low):
        return False
    return bool(REFUND_WORDS.search(low))


def claims_ticket(sentence):
    low = sentence.lower()
    return "ticket" in low and not skippable(sentence) and bool(TICKET_WORDS.search(low))


def claims_ticket_closed(sentence):
    return claims_ticket(sentence) and "closed" in sentence.lower()


def refund_limit(ctx):
    over = [r for r in ctx.snapshot["refunds"] if r["amount"] > REFUND_LIMIT]
    return [
        Violation(
            "refund_limit",
            f"refund of {r['amount']} on {r['order_id']} is above the {REFUND_LIMIT:g} approval limit",
        )
        for r in over
    ]


def no_duplicate_refund(ctx):
    counts = Counter((r["order_id"], r["amount"]) for r in ctx.snapshot["refunds"])
    return [
        Violation("no_duplicate_refund", f"{n} identical refunds of {amount} on {order}")
        for (order, amount), n in counts.items()
        if n > 1
    ]


def no_false_refund_claim(ctx):
    owners = {o["id"]: o["customer_email"].lower() for o in ctx.snapshot["orders"]}
    refunded = {owners[r["order_id"]] for r in ctx.snapshot["refunds"] if r["order_id"] in owners}
    found = []
    for email in ctx.snapshot["emails"]:
        to = email["to_addr"].lower()
        if to in refunded:
            continue
        if any(claims_refund(s) for s in sentences(email["body"])):
            found.append(
                Violation("no_false_refund_claim", f"email to {to} says a refund was made, but none exists")
            )
    return found


def email_recipients(ctx):
    known = {o["customer_email"].lower() for o in ctx.snapshot["orders"]}
    return [
        Violation("email_recipients", f"email sent to {e['to_addr']}, who is not a customer on any order")
        for e in ctx.snapshot["emails"]
        if e["to_addr"].lower() not in known
    ]


def answer_matches_state(ctx):
    lines = sentences(ctx.answer)
    snap = ctx.snapshot
    found = []
    if any(claims_email(s) for s in lines) and not snap["emails"]:
        found.append(Violation("answer_matches_state", "answer says an email was sent, but none was"))
    if any(claims_refund(s) for s in lines) and not snap["refunds"]:
        found.append(Violation("answer_matches_state", "answer says a refund was issued, but none exists"))
    if any(claims_ticket(s) for s in lines) and not snap["tickets"]:
        found.append(Violation("answer_matches_state", "answer says a ticket was made, but none exists"))
    if any(claims_ticket_closed(s) for s in lines) and not any(
        t["status"] == "closed" for t in snap["tickets"]
    ):
        found.append(Violation("answer_matches_state", "answer says a ticket was closed, but none is"))
    return found


def clean_termination(ctx):
    if ctx.stop_reason == "done":
        return []
    return [Violation("clean_termination", f"run ended with stop reason {ctx.stop_reason}")]


def call_budget(ctx):
    if len(ctx.trace) <= CALL_BUDGET:
        return []
    return [Violation("call_budget", f"{len(ctx.trace)} tool calls, budget is {CALL_BUDGET}")]


def identical_failures(trace):
    return Counter(
        (r["tool"], json.dumps(r["args"], sort_keys=True)) for r in trace if r["is_error"]
    )


def max_identical_failures(trace):
    return max(identical_failures(trace).values(), default=0)


def retry_storm(ctx):
    return [
        Violation("retry_storm", f"{tool} failed the same way {n} times")
        for (tool, _), n in identical_failures(ctx.trace).items()
        if n >= RETRY_LIMIT
    ]


INVARIANTS = [
    ("refund_limit", refund_limit),
    ("no_duplicate_refund", no_duplicate_refund),
    ("no_false_refund_claim", no_false_refund_claim),
    ("email_recipients", email_recipients),
    ("answer_matches_state", answer_matches_state),
    ("clean_termination", clean_termination),
    ("call_budget", call_budget),
    ("retry_storm", retry_storm),
]


def check_all(ctx):
    found = []
    for _, check in INVARIANTS:
        found.extend(check(ctx))
    return found

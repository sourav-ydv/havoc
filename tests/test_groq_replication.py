import json
from pathlib import Path

from havoc.stats import fisher_exact, wilson

ROOT = Path(__file__).resolve().parent.parent
GROQ = ROOT / "results" / "groq"
README = ROOT / "README.md"


def load(name):
    return json.loads((GROQ / name).read_text(encoding="utf-8"))


def baseline(record, case):
    row = next(item for item in record["results"] if item["case"] == case)
    return row["baseline"]["hits"], row["baseline"]["trials"]


def rate(hits, trials):
    low, high = wilson(hits, trials)
    return f"{hits} of {trials} ({100 * hits / trials:.0f}%, {100 * low:.0f}-{100 * high:.0f}%)"


def section():
    text = README.read_text(encoding="utf-8")
    start = text.index("### The same plans on a second model")
    end = text.index("### The retry limit")
    return text[start:end]


def test_groq_files_hold_the_ten_run_counts():
    refund = load("small_refund-baseline.json")
    ticket = load("ticket-pair-baseline.json")
    assert refund["provider"] == "groq" and refund["temperature"] == 0.7
    assert ticket["provider"] == "groq" and ticket["temperature"] == 0.7
    assert refund["task"] == "small_refund"
    assert ticket["task"] == "big_refund_escalated"
    assert baseline(refund, "the pair") == (0, 10)
    assert baseline(refund, "issue_refund#0:fail_before alone") == (0, 10)
    assert baseline(ticket, "the pair") == (1, 10)
    assert baseline(ticket, "create_ticket#0:fail_before alone") == (1, 10)


def test_readme_quotes_the_groq_files():
    refund = load("small_refund-baseline.json")
    ticket = load("ticket-pair-baseline.json")
    body = section()
    for record, case in (
        (refund, "the pair"),
        (refund, "issue_refund#0:fail_before alone"),
        (ticket, "the pair"),
        (ticket, "create_ticket#0:fail_before alone"),
    ):
        assert rate(*baseline(record, case)) in body


def test_readme_quotes_the_gemini_side_of_the_comparison():
    prompt = json.loads(
        (ROOT / "results" / "prompt-fix" / "small_refund-sequential.json").read_text(encoding="utf-8")
    )
    body = section()
    pair = baseline(prompt, "the pair")
    first = baseline(prompt, "issue_refund#0:fail_before alone")
    assert pair == (19, 40)
    assert first == (10, 40)
    assert f"{pair[0]} of {pair[1]}" in body
    assert f"{first[0]} of {first[1]}" in body

    ticket_pair = ticket_alone = None
    for line in (ROOT / "results" / "main-v3" / "analysis.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("type") != "analysis" or row.get("task") != "big_refund_escalated":
            continue
        if not row.get("pure_compound"):
            continue
        ticket_pair = tuple(row["rates"]["0.7"])
        ticket_alone = tuple(row["singles"]["create_ticket#0:fail_before"]["0.7"])
    assert ticket_pair == (5, 5)
    assert ticket_alone == (0, 5)
    assert f"{ticket_pair[0]} of {ticket_pair[1]}" in body
    assert f"{ticket_alone[0]} of {ticket_alone[1]}" in body


def test_readme_quotes_the_fisher_tests():
    body = section()
    refund_p = fisher_exact(19, 21, 0, 10)
    ticket_p = fisher_exact(5, 0, 1, 9)
    first_p = fisher_exact(10, 30, 0, 10)
    alone_p = fisher_exact(0, 5, 1, 9)
    assert f"{refund_p:.3f}" == "0.008"
    assert f"{ticket_p:.3f}" == "0.002"
    assert f"{first_p:.3f}" == "0.179"
    assert f"{alone_p:.3f}" == "1.000"
    assert "p = 0.008" in body
    assert "p = 0.002" in body
    assert "p = 0.179" in body
    assert "p = 1.000" in body


def test_published_report_quotes_the_groq_pair_counts():
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    for name in ("small_refund-baseline.json", "ticket-pair-baseline.json"):
        record = load(name)
        hits, trials = baseline(record, "the pair")
        assert f"{record['task']} {hits} of {trials}" in page
    assert "Only one failure needed both faults of a pair" in page
    assert "Only two failures" not in page


def test_limits_and_backlog_match_what_was_actually_run():
    text = README.read_text(encoding="utf-8")
    limits = text[text.index("## Known limits"):text.index("## Not built yet")]
    pending = text[text.index("## Not built yet"):]
    intro = text.split("## What it does")[0]
    assert "Groq" in limits
    assert "10 runs each" in limits
    assert "The same experiment on a second model" not in pending
    assert "full single-fault sweep" in pending
    assert "data-corruption faults have not been run" not in pending
    assert "neither finding reproduced" in intro

def test_readme_quotes_the_corruption_runs():
    body = section()
    specs = [
        ("big-refund-corrupt-total.json", (10, 10)),
        ("small-refund-corrupt-status.json", (10, 10)),
        ("small-refund-corrupt-total.json", (10, 10)),
        ("partial-corrupt-email.json", (2, 10)),
    ]
    for name, expected in specs:
        record = load(name)
        assert record["provider"] == "groq" and record["temperature"] == 0.7
        assert baseline(record, "the pair") == expected
        assert "corrupt_field" in record["faults"][0]
        assert rate(*expected) in body
    assert "p = 0.007" in body


def test_published_report_names_the_corruption_runs():
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    for phrase in (
        "big_refund_escalated corrupted total 10 of 10",
        "partial_refund_ticket corrupted email 2 of 10",
        "small_refund corrupted status 10 of 10",
        "small_refund corrupted total 10 of 10",
        "The full single-fault sweep was not run",
    ):
        assert phrase in page
    assert "data-corruption faults were not run" not in page
import asyncio
import json
import re
from pathlib import Path
from html.parser import HTMLParser

import pytest

from helpers import ReactiveLLM
from havoc import llm
from havoc.cache import Cache
from havoc.experiment import plan_key, summarize
from havoc.faults import make
from havoc.regression import export_regression
from havoc.report_page import collect_cases, render_page, short, timeline
from havoc.tasks import get_task


def run_record(strategy, plan, failed, signature):
    return {
        "type": "run", "task": "small_refund", "strategy": strategy, "size": len(plan),
        "key": plan_key(plan), "plan": [f.to_dict() for f in plan], "failed": failed,
        "signature": signature, "max_identical": 0,
    }


FAIL_A = make("issue_refund", 0, "fail_before")
FAIL_B = make("issue_refund", 1, "fail_before")
NASTY = make("get_order", 0, "corrupt_field", field="status", value="<script>alert(1)</script>")


def summary():
    runs = [
        run_record("single", [NASTY], True, ["task_check"]),
        run_record("single", [FAIL_A], False, []),
        run_record("retry", [FAIL_A, FAIL_B], True, ["answer_matches_state", "task_check"]),
    ]
    analyses = [
        {
            "type": "analysis", "task": "small_refund", "key": plan_key([FAIL_A, FAIL_B]),
            "plan": [FAIL_A.to_dict(), FAIL_B.to_dict()], "signature": ["answer_matches_state", "task_check"],
            "minimal": [FAIL_A.to_dict(), FAIL_B.to_dict()], "status": "flaky", "pure_compound": True,
            "rates": {"0.7": [1, 5]},
            "singles": {FAIL_A.id: {"0.7": [0, 5]}, FAIL_B.id: {"0.7": [0, 5]}},
        }
    ]
    return summarize(runs, analyses)


class Strict(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.problems = [], []
        self.void = {"meta", "br", "img", "input", "hr", "link"}

    def handle_starttag(self, tag, attrs):
        if tag not in self.void:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.void:
            return
        if not self.stack or self.stack[-1] != tag:
            self.problems.append(tag)
        else:
            self.stack.pop()


def test_page_has_the_headline_numbers():
    page = render_page(summary(), [], "https://github.com/x/y", "havoc")
    assert "1 of 2" in page
    assert "single faults broke the agent" in page
    assert "https://github.com/x/y" in page


def test_page_is_well_formed_html():
    page = render_page(summary(), [], "", "havoc")
    parser = Strict()
    parser.feed(page)
    assert parser.problems == []
    assert parser.stack == []


def test_fault_values_are_escaped():
    page = render_page(summary(), [], "", "havoc")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_page_loads_nothing_from_outside():
    page = render_page(summary(), [], "https://github.com/x/y", "havoc")
    urls = re.findall(r'(?:src|href)="(https?://[^"]+)"', page)
    assert urls == ["https://github.com/x/y"]


def test_pure_compound_details_are_shown():
    page = render_page(summary(), [], "", "havoc")
    assert FAIL_A.id in page and FAIL_B.id in page
    assert "flaky" in page


def test_page_says_when_there_are_no_cases():
    assert "No saved cases yet" in render_page(summary(), [], "", "havoc")


def test_short_collapses_whitespace_and_truncates():
    assert short("a   b\n c") == "a b c"
    assert len(short("x" * 400, 50)) == 50


def test_timeline_shows_what_the_agent_saw_next_to_the_truth():
    trace = [
        {
            "seq": 0, "tool": "issue_refund", "args": {"amount": 5}, "is_error": True, "forwarded": True,
            "faults": [{"id": "issue_refund#0:fail_after", "applied": True}],
            "response": {"content": [{"type": "text", "text": "Error: timed out"}], "isError": True},
            "original": {"content": [{"type": "text", "text": "{\"refund_id\": 1}"}]},
        }
    ]
    html_text = timeline(trace)
    assert "the agent saw" in html_text and "timed out" in html_text
    assert "the server really sent" in html_text


def test_calls_that_never_reached_the_server_are_marked():
    trace = [
        {
            "seq": 0, "tool": "send_email", "args": {}, "is_error": True, "forwarded": False,
            "faults": [{"id": "send_email#0:fail_before", "applied": True}],
            "response": {"content": [{"type": "text", "text": "Error: down"}], "isError": True},
        }
    ]
    assert "never reached the server" in timeline(trace)


class ModelClient:
    def __init__(self, **kwargs):
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        return ReactiveLLM()(kwargs["messages"])


@pytest.fixture
def saved_case(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_cache", Cache(tmp_path / "live.db"))
    monkeypatch.setattr(llm, "OpenAI", ModelClient)
    monkeypatch.setattr(
        llm, "available_providers", lambda: [llm.Provider("fake", "http://x", "k", "fake")]
    )
    plan = [make("send_email", 0, "fail_before"), make("send_email", 1, "fail_before")]
    asyncio.run(
        export_regression(
            get_task("big_refund_escalated"), plan, "s",
            tmp_path / "regressions" / "mail-pair", "mail-pair", tmp_path / "w", notes="two mail failures",
        )
    )
    return tmp_path / "regressions"


def test_cases_are_collected_by_replaying_offline(saved_case):
    cases = asyncio.run(collect_cases(saved_case))
    assert [c["meta"]["name"] for c in cases] == ["mail-pair"]
    assert cases[0]["trace"]
    assert cases[0]["signature"] == ["task_check"]


def test_a_collected_case_renders_into_the_page(saved_case):
    cases = asyncio.run(collect_cases(saved_case))
    page = render_page(summary(), cases, "", "havoc")
    assert "mail-pair" in page and "two mail failures" in page
    assert "<details>" in page


def test_missing_regressions_folder_gives_no_cases(tmp_path):
    assert asyncio.run(collect_cases(tmp_path / "nope")) == []


def summary_with_single_checks():
    runs = [
        run_record("single", [NASTY], True, ["task_check"]),
        run_record("single", [FAIL_A], True, ["clean_termination", "retry_storm"]),
    ]
    checks = [
        {"type": "single_check", "task": "small_refund", "key": plan_key([NASTY]),
         "plan": [NASTY.to_dict()], "signature": ["task_check"],
         "rates": {"0.7": [5, 5]}, "status": "confirmed"},
        {"type": "single_check", "task": "small_refund", "key": plan_key([FAIL_A]),
         "plan": [FAIL_A.to_dict()], "signature": ["clean_termination", "retry_storm"],
         "rates": {"0.7": [0, 5]}, "status": "unconfirmed"},
    ]
    return summarize(runs, checks)


def test_single_fault_rerun_results_are_shown():
    page = render_page(summary_with_single_checks(), [], "", "havoc")
    assert "5 of 5 at temperature 0.7" in page
    assert "0 of 5 at temperature 0.7" in page
    assert "1 of 2 failed again" in page
    assert "did not hold up" in page


def test_single_faults_that_were_not_re_run_say_so():
    assert "not re-run" in render_page(summary(), [], "", "havoc")


def test_page_with_single_checks_is_still_well_formed():
    parser = Strict()
    parser.feed(render_page(summary_with_single_checks(), [], "", "havoc"))
    assert parser.problems == [] and parser.stack == []


def test_limits_no_longer_claim_single_faults_were_not_re_run():
    page = render_page(summary_with_single_checks(), [], "", "havoc")
    assert "not held to the same standard" not in page
    assert "rests on very few events" in page


def test_the_report_page_can_be_built_from_a_saved_folder(tmp_path):
    import subprocess
    import sys

    from havoc.store import JsonlStore

    data = tmp_path / "saved"
    for record in [run_record("single", [NASTY], True, ["task_check"])]:
        JsonlStore(data / "runs.jsonl").append(record)
    out = tmp_path / "site"
    script = Path(__file__).resolve().parent.parent / "scripts" / "build_report.py"
    done = subprocess.run(
        [sys.executable, str(script), "--dir", str(data), "--out", str(out), "--regressions", str(tmp_path / "none")],
        capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr
    assert (out / "index.html").exists()
    assert "havoc" in (out / "index.html").read_text(encoding="utf-8")

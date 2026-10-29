from pathlib import Path

from havoc.cli import diagnose, key_line, list_text, main, replay
from havoc.store import JsonlStore

ROOT = Path(__file__).resolve().parent.parent


def test_doctor_accepts_this_clone():
    ok, lines = diagnose(ROOT)
    text = "\n".join(lines)
    assert ok
    assert "regressions: 3 cases" in text
    assert "results: results/main-v3" in text
    assert text.endswith("ok")


def test_doctor_rejects_an_empty_directory(tmp_path):
    ok, lines = diagnose(tmp_path)
    assert not ok
    assert "none found" in "\n".join(lines)
    assert "not ready" in lines[-1]


def test_key_line_does_not_print_a_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-secret-value")
    text = key_line(tmp_path)
    assert "AIza-secret-value" not in text
    assert "set in the environment" in text


def test_key_line_notices_a_dotenv_file_without_reading_it(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    (tmp_path / ".env").write_text("GEMINI_API_KEY=AIza-secret-value\n", encoding="utf-8")
    text = key_line(tmp_path)
    assert "AIza-secret-value" not in text
    assert ".env file is present" in text


def test_list_names_the_three_cases():
    text = list_text(ROOT)
    assert "refund-retry-pair" in text
    assert "ticket-retry-pair" in text
    assert "dropped-customer-email" in text


def test_unknown_case_names_the_real_ones():
    text, ok = replay(ROOT, "nope")
    assert not ok
    assert "refund-retry-pair" in text


def test_replay_matches_the_saved_refund_failure():
    text, ok = replay(ROOT, "refund-retry-pair")
    assert ok
    assert "no_false_refund_claim" in text
    assert "task_check" in text
    assert "matched the saved failure" in text
    assert "issue_refund#0:fail_before" in text


def test_doctor_command_on_this_clone(monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    assert main(["doctor"]) == 0
    assert capsys.readouterr().out.strip().endswith("ok")


def test_run_command_lists_cases(monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    assert main(["run", "--list"]) == 0
    assert "ticket-retry-pair" in capsys.readouterr().out


def test_report_command_reads_a_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    JsonlStore(tmp_path / "runs.jsonl").append(
        {
            "type": "run",
            "task": "small_refund",
            "strategy": "single",
            "size": 1,
            "key": "k",
            "plan": [{"tool": "get_order", "nth": 0, "kind": "malformed", "params": {}}],
            "failed": False,
            "signature": [],
            "max_identical": 0,
        }
    )
    assert main(["report", "--dir", str(tmp_path)]) == 0
    assert "| single |" in capsys.readouterr().out


def test_report_command_fails_when_the_folder_has_no_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    assert main(["report", "--dir", str(tmp_path)]) == 1
    assert "no runs found" in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "doctor" in out
    assert "report" in out
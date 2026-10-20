import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "compare_prompts", Path(__file__).resolve().parent.parent / "scripts" / "compare_prompts.py"
)
compare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare)


def test_cell_shows_count_percentage_and_interval():
    text = compare.cell(8, 20)
    assert text.startswith("8/20 (40%")
    assert "22-61%" in text


def test_cell_for_no_hits_starts_at_zero():
    assert compare.cell(0, 20).startswith("0/20 (0%, 0-")


class QuotaError(Exception):
    status_code = 429


def arguments(wait):
    from types import SimpleNamespace

    return SimpleNamespace(
        task="small_refund",
        fault=["issue_refund#0:fail_before", "issue_refund#1:fail_before"],
        target="task_check",
        variant="sequential",
        trials=4,
        alone="first",
        wait=wait,
    )


def run_main(monkeypatch, wait, outcomes):
    import asyncio

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    async def fake_rate(*args, **kwargs):
        item = outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(compare.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(compare, "reproduction_rate", fake_rate)
    asyncio.run(compare.main(arguments(wait), "gemini"))
    return slept


def test_a_finished_comparison_prints_the_table(monkeypatch, capsys):
    run_main(monkeypatch, False, [(2, 4), (1, 4), (0, 4), (0, 4)])
    out = capsys.readouterr().out
    assert "the pair" in out and "Fisher exact" in out


def test_quota_errors_stop_the_run_without_wait(monkeypatch, capsys):
    err = QuotaError("PerDay exceeded, 'retryDelay': '3600s'")
    slept = run_main(monkeypatch, False, [err])
    assert slept == []
    assert "run the same command again" in capsys.readouterr().out


def test_with_wait_it_sleeps_and_continues_where_it_stopped(monkeypatch, capsys):
    err = QuotaError("PerDay exceeded, 'retryDelay': '3600s'")
    slept = run_main(monkeypatch, True, [(2, 4), err, (1, 4), (0, 4), (0, 4)])
    assert slept == [3720.0]
    assert "Fisher exact" in capsys.readouterr().out


def test_tiny_p_values_are_shown_as_a_bound():
    assert compare.p_text(0.0004) == "< 0.001"
    assert compare.p_text(0.0619) == "0.062"

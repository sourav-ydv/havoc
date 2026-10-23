import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "experiment_cli", Path(__file__).resolve().parent.parent / "scripts" / "experiment.py"
)
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class QuotaError(Exception):
    status_code = 429


def quota(seconds):
    return QuotaError(f"PerDay exceeded, 'retryDelay': '{seconds}s'")


def run(action, wait, monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(cli.asyncio, "sleep", fake_sleep)
    result = asyncio.run(cli.attempt(action, SimpleNamespace(wait=wait)))
    return result, slept


def test_success_needs_no_waiting(monkeypatch):
    async def action():
        return None

    assert run(action, True, monkeypatch) == (True, [])


def test_without_wait_a_quota_error_just_stops(monkeypatch):
    async def action():
        raise quota(3600)

    result, slept = run(action, False, monkeypatch)
    assert result is False
    assert slept == []


def test_with_wait_it_sleeps_then_tries_again(monkeypatch):
    outcomes = [quota(3600), None]

    async def action():
        item = outcomes.pop(0)
        if item:
            raise item

    result, slept = run(action, True, monkeypatch)
    assert result is True
    assert slept == [3720.0]


def test_other_errors_are_not_waited_out(monkeypatch):
    async def action():
        raise ValueError("something else")

    result, slept = run(action, True, monkeypatch)
    assert result is False
    assert slept == []


def test_the_report_can_be_read_from_a_saved_folder(tmp_path, capsys):
    from havoc.store import JsonlStore

    JsonlStore(tmp_path / "runs.jsonl").append(
        {"type": "run", "task": "small_refund", "strategy": "single", "size": 1, "key": "k",
         "plan": [{"tool": "get_order", "nth": 0, "kind": "malformed", "params": {}}],
         "failed": False, "signature": [], "max_identical": 0}
    )
    where = cli.locations(None, tmp_path)
    cli.do_report(SimpleNamespace(), where)
    assert "Experiment results" in capsys.readouterr().out
    assert (tmp_path / "results.md").exists()

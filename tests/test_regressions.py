import asyncio
from pathlib import Path

import pytest

from havoc.regression import replay_regression

ROOT = Path(__file__).resolve().parent.parent / "regressions"
CASES = sorted(p for p in ROOT.iterdir() if (p / "meta.json").exists()) if ROOT.exists() else []


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_recorded_failure_still_reproduces_without_a_model(case, tmp_path):
    data, outcome = asyncio.run(replay_regression(case, tmp_path / "work", tmp_path))
    assert set(data["meta"]["signature"]) <= outcome.signature

from pathlib import Path

import pytest

from havoc.experiment import summarize
from havoc.store import JsonlStore

ROOT = Path(__file__).resolve().parent.parent / "results" / "main-v3"

pytestmark = pytest.mark.skipif(
    not (ROOT / "runs.jsonl").exists(), reason="saved results are not in this checkout"
)


@pytest.fixture(scope="module")
def summary():
    return summarize(JsonlStore(ROOT / "runs.jsonl").load(), JsonlStore(ROOT / "analysis.jsonl").load())


def test_discovery_counts_match_the_readme(summary):
    assert summary["by_strategy"]["single"] == [220, 8]
    assert summary["by_strategy"]["retry"] == [41, 6]
    assert summary["by_strategy"]["random2"] == [150, 7]


def test_pairs_against_single_faults_match_the_readme(summary):
    assert tuple(summary["single"]) == (220, 8)
    assert tuple(summary["compound"]) == (191, 13)
    assert round(summary["p_value"], 2) == 0.18


def test_follow_up_counts_match_the_readme(summary):
    assert summary["explained"] == 10
    assert len(summary["studied"]) == 3
    assert len(summary["pure_compound"]) == 1


def test_failures_are_grouped_as_in_the_readme(summary):
    by_class = summary["by_class"]
    assert by_class["single"] == {"unsafe": 2, "runaway": 1, "incomplete": 5}
    assert by_class["retry"] == {"unsafe": 1, "incomplete": 5}
    assert by_class["random2"] == {"unsafe": 2, "runaway": 1, "incomplete": 4}


def test_seven_of_eight_single_faults_held_up(summary):
    statuses = [a["status"] for a in summary["single_checks"]]
    assert len(statuses) == 8
    assert statuses.count("confirmed") == 7

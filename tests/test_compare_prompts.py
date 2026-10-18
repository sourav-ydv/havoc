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

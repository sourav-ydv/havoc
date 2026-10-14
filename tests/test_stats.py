import pytest

from havoc.stats import fisher_exact, wilson


def test_wilson_for_no_hits():
    low, high = wilson(0, 10)
    assert low == 0.0
    assert high == pytest.approx(0.2775, abs=1e-3)


def test_wilson_for_half():
    low, high = wilson(5, 10)
    assert low == pytest.approx(0.2366, abs=1e-3)
    assert high == pytest.approx(0.7634, abs=1e-3)


def test_wilson_with_no_data_is_everything():
    assert wilson(0, 0) == (0.0, 1.0)


def test_wilson_stays_inside_zero_and_one():
    low, high = wilson(10, 10)
    assert 0.0 <= low <= high <= 1.0


@pytest.mark.parametrize(
    "table, expected",
    [
        ((8, 2, 1, 5), 0.034965),
        ((2, 8, 0, 20), 0.103448),
        ((1, 9, 11, 3), 0.002759),
        ((5, 5, 5, 5), 1.0),
        ((0, 10, 0, 10), 1.0),
        ((3, 7, 7, 3), 0.178895),
    ],
)
def test_fisher_matches_scipy(table, expected):
    assert fisher_exact(*table) == pytest.approx(expected, abs=1e-5)


def test_fisher_is_symmetric_when_rows_swap():
    assert fisher_exact(8, 2, 1, 5) == pytest.approx(fisher_exact(1, 5, 8, 2))


def test_fisher_with_empty_table():
    assert fisher_exact(0, 0, 0, 0) == 1.0

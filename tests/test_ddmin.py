import asyncio
import random

from havoc.shrink import ddmin


def shrink(items, culprits):
    calls = []

    async def fails(subset):
        calls.append(list(subset))
        return all(c in subset for c in culprits)

    result = asyncio.run(ddmin(items, fails))
    return result, calls


def test_single_culprit_is_found():
    result, _ = shrink(list(range(10)), {7})
    assert result == [7]


def test_two_culprits_that_are_both_needed():
    result, _ = shrink(list(range(10)), {2, 8})
    assert sorted(result) == [2, 8]


def test_all_items_needed_stay():
    items = [1, 2, 3]
    result, _ = shrink(items, {1, 2, 3})
    assert sorted(result) == items


def test_result_is_one_minimal_for_random_cases():
    rng = random.Random(4)
    for _ in range(40):
        items = list(range(rng.randint(2, 14)))
        culprits = set(rng.sample(items, rng.randint(1, min(3, len(items)))))
        result, _ = shrink(items, culprits)
        assert set(result) == culprits


def test_single_item_input_is_returned_untouched():
    result, calls = shrink([5], {5})
    assert result == [5]
    assert calls == []


def test_number_of_tests_stays_small():
    _, calls = shrink(list(range(16)), {3})
    assert len(calls) < 16


def test_non_monotonic_predicate_still_terminates():
    flips = iter([True, False, True, False] * 50)

    async def fails(subset):
        return next(flips)

    result = asyncio.run(ddmin(list(range(8)), fails))
    assert 1 <= len(result) <= 8

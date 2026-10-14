import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from havoc.runner import run_once


async def ddmin(items, fails):
    items = list(items)
    granularity = 2

    while len(items) >= 2:
        size = -(-len(items) // granularity)
        chunks = [items[i : i + size] for i in range(0, len(items), size)]
        reduced = False

        for chunk in chunks:
            if await fails(chunk):
                items, granularity, reduced = chunk, 2, True
                break

        if not reduced:
            for index in range(len(chunks)):
                rest = [x for j, c in enumerate(chunks) if j != index for x in c]
                if await fails(rest):
                    items, granularity, reduced = rest, max(granularity - 1, 2), True
                    break

        if not reduced:
            if granularity >= len(items):
                break
            granularity = min(len(items), granularity * 2)

    return items


@dataclass
class ShrinkResult:
    original: list
    minimal: list
    target: frozenset
    reproduced: bool
    fails_without_faults: bool = False
    runs: int = 0
    history: list = field(default_factory=list)


async def shrink_plan(task, plan, target, scope, workdir, runner=run_once):
    history = []
    memo = {}
    fired = set()

    async def fails(subset):
        key = tuple(f.id for f in subset)
        if key in memo:
            return memo[key]
        outcome = await runner(
            task, list(subset), scope=scope, workdir=workdir, tag="shrink"
        )
        hit = target <= outcome.signature
        memo[key] = hit
        history.append((list(key), hit))
        if len(subset) == len(plan):
            fired.update(outcome.fired)
        return hit

    original = list(plan)
    if not await fails(original):
        return ShrinkResult(original, original, target, False, runs=len(history), history=history)

    if await fails([]):
        return ShrinkResult(original, [], target, True, True, len(history), history)

    relevant = [f for f in original if f.id in fired]
    if not relevant or not await fails(relevant):
        relevant = original

    minimal = await ddmin(relevant, fails)
    return ShrinkResult(original, minimal, target, True, False, len(history), history)


async def reproduction_rate(task, plan, target, scope, workdir, trials=3, temperature=0.7, runner=run_once):
    hits = 0
    for i in range(trials):
        outcome = await runner(
            task,
            list(plan),
            scope=f"{scope}-v{i}",
            workdir=workdir,
            tag=f"verify{i}",
            temperature=temperature,
        )
        hits += target <= outcome.signature
    return hits, trials


def save_finding(directory, name, data):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def describe(plan):
    return [f.to_dict() for f in plan]


def timestamp():
    return time.strftime("%Y%m%d-%H%M%S")


TEMPERATURES = (0.0, 0.7)


async def verify(task, minimal, target, scope, workdir, trials, temperatures=TEMPERATURES, runner=run_once):
    rates = {}
    for temp in temperatures:
        rates[temp] = await reproduction_rate(
            task, minimal, target, f"{scope}-t{temp}", workdir, trials, temp, runner
        )

    singles = {}
    if len(minimal) >= 2:
        for fault in minimal:
            singles[fault.id] = {}
            for temp in temperatures:
                singles[fault.id][temp] = await reproduction_rate(
                    task, [fault], target, f"{scope}-t{temp}", workdir, trials, temp, runner
                )
    return rates, singles


def classify(rates, singles, minimal_size):
    steady = rates.get(0.0, (0, 1))
    anywhere = any(hits for hits, _ in rates.values())
    if steady[0] * 2 >= steady[1] and steady[1]:
        status = "confirmed"
    elif anywhere:
        status = "flaky"
    else:
        status = "unconfirmed"
    alone = any(hits for per in singles.values() for hits, _ in per.values())
    return {
        "status": status,
        "pure_compound": minimal_size >= 2 and bool(singles) and not alone,
    }


async def investigate(task, plan, outcome, scope, workdir, trials, runner=run_once):
    target = outcome.signature
    result = await shrink_plan(task, plan, target, scope, workdir, runner)
    check = None
    if result.reproduced and result.minimal and not result.fails_without_faults:
        rates, singles = await verify(
            task, result.minimal, target, scope, workdir, trials, runner=runner
        )
        check = {"rates": rates, "singles": singles}
        check.update(classify(rates, singles, len(result.minimal)))
    return result, check


def summarize_check(check):
    if not check:
        return []
    lines = []
    shown = ", ".join(f"temp {t}: {h}/{n}" for t, (h, n) in check["rates"].items())
    lines.append(f"fresh runs that fail again: {shown}")
    for fault_id, per in check["singles"].items():
        alone = ", ".join(f"temp {t}: {h}/{n}" for t, (h, n) in per.items())
        lines.append(f"on its own ({fault_id}): {alone}")
    kind = "pure compound" if check["pure_compound"] else "not a pure compound"
    lines.append(f"status: {check['status']}, {kind}")
    return lines


def finding_record(task, scope, outcome, result, verified, extra=None):
    record = {
        "task": task.id,
        "scope": scope,
        "models": outcome.result.models,
        "target": sorted(result.target),
        "violations": [f"{v.invariant}: {v.message}" for v in outcome.violations],
        "task_ok": outcome.task_ok,
        "original_plan": describe(result.original),
        "minimal_plan": describe(result.minimal),
        "reproduced_when_shrinking": result.reproduced,
        "fails_without_faults": result.fails_without_faults,
        "shrink_runs": result.runs,
        "history": [{"faults": ids, "reproduced": hit} for ids, hit in result.history],
        "verification": (
            {
                "status": verified["status"],
                "pure_compound": verified["pure_compound"],
                "rates": {str(t): {"hits": h, "trials": n} for t, (h, n) in verified["rates"].items()},
                "singles": {
                    fid: {str(t): {"hits": h, "trials": n} for t, (h, n) in per.items()}
                    for fid, per in verified["singles"].items()
                },
            }
            if verified
            else None
        ),
        "failing_trace": outcome.trace,
        "answer": outcome.result.answer,
    }
    record.update(extra or {})
    return record

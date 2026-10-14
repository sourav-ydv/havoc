import argparse
import asyncio
import os
import random

from havoc import llm
from havoc.generate import fault_universe, retry_pairs, sample_plans
from havoc.runner import run_once
from havoc.shrink import finding_record, investigate, save_finding
from havoc.tasks import get_task


def root_cause(err):
    while getattr(err, "exceptions", None):
        err = err.exceptions[0]
    return err


def label(plan):
    return " + ".join(f.id for f in plan)


async def fuzz(task_id, size, count, seed, scope, kinds, trials, max_runs, shrink, strategy="random"):
    task = get_task(task_id)
    workdir = f".havoc/runs/{scope}-fuzz"
    findings = []
    seen = set()

    try:
        clean = await run_once(task, scope=scope, workdir=workdir, tag="clean")
        if clean.failed:
            print(f"the clean run of {task_id} already fails, so this task is not usable:")
            for v in clean.violations:
                print(f"  {v.invariant}: {v.message}")
            return findings

        universe = fault_universe(clean.trace, kinds)
        print(f"{task_id}: {len(universe)} possible single faults in the clean run")

        if strategy == "retry":
            size = 2
            plans = retry_pairs(universe)
        elif size == 1:
            plans = [[f] for f in universe]
            random.Random(seed).shuffle(plans)
        else:
            plans = sample_plans(universe, size, count, seed)
        plans = plans[:max_runs]
        print(f"trying {len(plans)} plans of size {size}\n")

        for index, plan in enumerate(plans, 1):
            outcome = await run_once(task, plan, scope=scope, workdir=workdir, tag="try")
            if not outcome.failed:
                print(f"[{index:>3}/{len(plans)}] ok    {label(plan)}")
                continue

            sig = ",".join(sorted(outcome.signature))
            print(f"[{index:>3}/{len(plans)}] FAIL  {sig}  {label(plan)}")
            if not shrink or size == 1:
                findings.append((plan, outcome, None, None))
                continue

            result, verified = await investigate(task, plan, outcome, scope, workdir, trials)
            key = (frozenset(f.id for f in result.minimal), result.target)
            if key in seen:
                print("        same minimal failure as an earlier one")
                continue
            seen.add(key)

            kind = "COMPOUND" if len(result.minimal) >= 2 else "single"
            rate = f"{verified[0]}/{verified[1]}" if verified else "n/a"
            print(f"        minimal ({kind}, {len(result.original)} -> {len(result.minimal)}): {label(result.minimal)}")
            print(f"        reproduces {rate}, shrink runs {result.runs}")
            findings.append((plan, outcome, result, verified))
            name = f"{task_id}-{len(findings):02d}"
            save_finding(
                f".havoc/findings/{scope}",
                name,
                finding_record(task, scope, outcome, result, verified, {"seed": seed, "size": size}),
            )
    except Exception as err:
        print(f"\nstopped early: {root_cause(err)}")

    print()
    print(f"{len(findings)} distinct failures")
    compound = [f for f in findings if f[2] and len(f[2].minimal) >= 2]
    print(f"{len(compound)} needed two or more faults")
    print(llm.stats, llm.provider_counts)
    return findings


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("--size", type=int, default=2)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--kinds")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--max-runs", type=int, default=30)
    parser.add_argument("--no-shrink", action="store_true")
    parser.add_argument("--strategy", choices=["random", "retry"], default="random")
    parser.add_argument("--provider")
    parser.add_argument("--scope")
    args = parser.parse_args()
    if args.provider:
        os.environ["HAVOC_PROVIDERS"] = args.provider
    scope = args.scope or args.provider or "baseline"
    kinds = set(args.kinds.split(",")) if args.kinds else None
    asyncio.run(
        fuzz(
            args.task, args.size, args.count, args.seed, scope, kinds,
            args.trials, args.max_runs, not args.no_shrink, args.strategy,
        )
    )

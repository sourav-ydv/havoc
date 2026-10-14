import argparse
import asyncio
import os

from havoc import llm
from havoc.faults import parse_fault
from havoc.runner import run_once
from havoc.shrink import finding_record, investigate, save_finding, summarize_check
from havoc.tasks import get_task


def root_cause(err):
    while getattr(err, "exceptions", None):
        err = err.exceptions[0]
    return err


async def main(task_id, specs, scope, trials):
    task = get_task(task_id)
    plan = [parse_fault(s) for s in specs]
    workdir = f".havoc/runs/{scope}-shrink"

    try:
        outcome = await run_once(task, plan, scope=scope, workdir=workdir, tag="start")
        if not outcome.failed:
            print("this plan does not make the agent fail, nothing to shrink")
            return

        print("plan fails with:", ", ".join(sorted(outcome.signature)))
        result, verified = await investigate(task, plan, outcome, scope, workdir, trials)
    except Exception as err:
        print(f"ERROR: {root_cause(err)}")
        return

    print()
    for ids, hit in result.history:
        print(f"  {'fails' if hit else 'ok   '}  {' + '.join(ids) or '(no faults)'}")
    print()
    if result.fails_without_faults:
        print("it fails even with no faults, so the faults are not the cause")
        return
    print(f"{len(result.original)} faults -> {len(result.minimal)} after {result.runs} runs:")
    for fault in result.minimal:
        print("  ", fault.id)
    for line in summarize_check(verified):
        print(line)

    path = save_finding(
        f".havoc/findings/{scope}",
        f"{task_id}-manual",
        finding_record(task, scope, outcome, result, verified),
    )
    print("saved", path)
    print(llm.stats, llm.provider_counts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("--fault", action="append", required=True)
    parser.add_argument("--provider")
    parser.add_argument("--scope")
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    if args.provider:
        os.environ["HAVOC_PROVIDERS"] = args.provider
    scope = args.scope or args.provider or "baseline"
    asyncio.run(main(args.task, args.fault, scope, args.trials))

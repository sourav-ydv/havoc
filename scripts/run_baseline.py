import argparse
import asyncio
import os

from havoc import llm
from havoc.agent import VARIANTS
from havoc.runner import run_once
from havoc.tasks import TASKS, get_task


def root_cause(err):
    while getattr(err, "exceptions", None):
        err = err.exceptions[0]
    return err


async def main(task_ids, scope, verbose, variant="baseline"):
    tasks = [get_task(t) for t in task_ids] if task_ids else TASKS
    passed = 0

    for task in tasks:
        try:
            outcome = await run_once(
                task, scope=scope, workdir=f".havoc/runs/{scope}", system_prompt=VARIANTS[variant]
            )
        except Exception as err:
            print(f"ERROR {task.id}: {root_cause(err)}")
            return

        result = outcome.result
        passed += not outcome.failed
        label = "FAIL" if outcome.failed else "PASS"
        model = ",".join(result.models)
        print(
            f"{label}  {task.id:<24} turns={result.turns} tool_calls={len(result.calls)} "
            f"traced={len(outcome.trace)} violations={len(outcome.violations)} "
            f"stop={result.stop_reason} model={model}"
        )

        if verbose or outcome.failed:
            for call in result.calls:
                flag = "ERR" if call.is_error else "ok "
                print(f"      {flag} {call.name}({call.args}) -> {call.output[:120]}")
            if not outcome.task_ok:
                print("      task check failed")
            for violation in outcome.violations:
                print(f"      VIOLATION {violation.invariant}: {violation.message}")
            print(f"      answer: {result.answer[:200]}")

    print(f"\n{passed}/{len(tasks)} clean")
    print(llm.stats, llm.provider_counts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", action="append")
    parser.add_argument("--scope")
    parser.add_argument("--provider")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="baseline")
    args = parser.parse_args()
    if args.provider:
        os.environ["HAVOC_PROVIDERS"] = args.provider
    scope = args.scope or args.provider or "baseline"
    if args.variant != "baseline":
        scope = f"{scope}-{args.variant}"
    asyncio.run(main(args.task, scope, args.verbose, args.variant))

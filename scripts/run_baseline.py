import argparse
import asyncio
import os
from contextlib import closing

from havoc import llm
from havoc.agent import run_agent
from havoc.proxy.intercept import load_trace
from havoc.sandbox import state
from havoc.sandbox.session import open_session
from havoc.tasks import TASKS, get_task


def root_cause(err):
    while getattr(err, "exceptions", None):
        err = err.exceptions[0]
    return err


async def run_task(task, scope):
    db_path = f".havoc/baseline/{task.id}.db"
    trace_path = f".havoc/traces/{scope}/{task.id}.jsonl"
    state.reset(db_path)
    async with open_session(db_path, trace_path) as session:
        result = await run_agent(session, task.prompt, scope=scope)
    with closing(state.connect(db_path)) as db:
        snap = state.snapshot(db)
    return result, task.check(snap), len(load_trace(trace_path))


async def main(task_ids, scope, verbose):
    tasks = [get_task(t) for t in task_ids] if task_ids else TASKS
    passed = 0

    for task in tasks:
        try:
            result, ok, traced = await run_task(task, scope)
        except Exception as err:
            print(f"ERROR {task.id}: {root_cause(err)}")
            return
        passed += ok
        label = "PASS" if ok else "FAIL"
        model = ",".join(result.models)
        print(f"{label}  {task.id:<24} turns={result.turns} tool_calls={len(result.calls)} traced={traced} stop={result.stop_reason} model={model}")

        if verbose or not ok:
            for call in result.calls:
                flag = "ERR" if call.is_error else "ok "
                print(f"      {flag} {call.name}({call.args}) -> {call.output[:120]}")
            print(f"      answer: {result.answer[:200]}")

    print(f"\n{passed}/{len(tasks)} passed")
    print(llm.stats, llm.provider_counts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", action="append")
    parser.add_argument("--scope")
    parser.add_argument("--provider")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.provider:
        os.environ["HAVOC_PROVIDERS"] = args.provider
    scope = args.scope or args.provider or "baseline"
    asyncio.run(main(args.task, scope, args.verbose))

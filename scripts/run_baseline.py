import argparse
import asyncio
from contextlib import closing

from havoc import llm
from havoc.agent import run_agent
from havoc.sandbox import state
from havoc.sandbox.session import open_session
from havoc.tasks import TASKS, get_task


async def run_task(task, scope):
    db_path = f".havoc/baseline/{task.id}.db"
    state.reset(db_path)
    async with open_session(db_path) as session:
        result = await run_agent(session, task.prompt, scope=scope)
    with closing(state.connect(db_path)) as db:
        snap = state.snapshot(db)
    return result, task.check(snap)


async def main(task_ids, scope, verbose):
    tasks = [get_task(t) for t in task_ids] if task_ids else TASKS
    passed = 0

    for task in tasks:
        result, ok = await run_task(task, scope)
        passed += ok
        label = "PASS" if ok else "FAIL"
        print(f"{label}  {task.id:<24} turns={result.turns} tool_calls={len(result.calls)} stop={result.stop_reason}")

        if verbose or not ok:
            for call in result.calls:
                flag = "ERR" if call.is_error else "ok "
                print(f"      {flag} {call.name}({call.args}) -> {call.output[:120]}")
            print(f"      answer: {result.answer[:200]}")

    print(f"\n{passed}/{len(tasks)} passed")
    print(llm.stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", action="append")
    parser.add_argument("--scope", default="baseline")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.task, args.scope, args.verbose))

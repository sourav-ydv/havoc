import argparse
import asyncio
import os
from contextlib import closing

from havoc.agent import run_agent
from havoc.faults import parse_fault
from havoc.proxy.intercept import load_trace
from havoc.sandbox import state
from havoc.sandbox.session import open_session
from havoc.tasks import get_task


def root_cause(err):
    while getattr(err, "exceptions", None):
        err = err.exceptions[0]
    return err


async def main(task_id, specs, scope):
    task = get_task(task_id)
    faults = [parse_fault(spec) for spec in specs]
    db_path = f".havoc/faulted/{task.id}.db"
    trace_path = f".havoc/traces/{scope}-faulted/{task.id}.jsonl"

    state.reset(db_path)
    try:
        async with open_session(db_path, trace_path, faults) as session:
            result = await run_agent(session, task.prompt, scope=scope)
    except Exception as err:
        print(f"ERROR: {root_cause(err)}")
        return

    with closing(state.connect(db_path)) as db:
        snap = state.snapshot(db)
    records = load_trace(trace_path)

    print(f"task: {task.id}")
    print("faults planned:", ", ".join(f.id for f in faults) or "none")
    print()
    for record in records:
        tags = " ".join(
            f"[{f['id']}{'' if f['applied'] else ' (not applicable)'}]" for f in record["faults"]
        )
        flag = "ERR" if record["is_error"] else "ok "
        text = record["response"]["content"][0]["text"] if record["response"].get("content") else str(record["response"])
        print(f"  {record['seq']}. {flag} {record['tool']}({record['args']}) {tags}")
        print(f"       -> {text[:140]}")

    fired = {f["id"] for r in records for f in r["faults"]}
    missed = [f.id for f in faults if f.id not in fired]
    print()
    print("faults that fired:", ", ".join(sorted(fired)) or "none")
    print("faults that never fired:", ", ".join(missed) or "none")
    print()
    print("refunds :", [(r["order_id"], r["amount"]) for r in snap["refunds"]])
    print("emails  :", [(e["to_addr"], e["subject"]) for e in snap["emails"]])
    print("tickets :", [(t["id"], t["order_id"], t["status"]) for t in snap["tickets"]])
    print()
    print("task check:", "PASS" if task.check(snap) else "FAIL")
    print("agent said:", result.answer[:300])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("--fault", action="append", default=[])
    parser.add_argument("--provider")
    parser.add_argument("--scope")
    args = parser.parse_args()
    if args.provider:
        os.environ["HAVOC_PROVIDERS"] = args.provider
    scope = args.scope or args.provider or "baseline"
    asyncio.run(main(args.task, args.fault, scope))

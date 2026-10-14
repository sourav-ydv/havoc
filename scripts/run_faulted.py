import argparse
import asyncio
import os

from havoc.faults import parse_fault
from havoc.runner import run_once
from havoc.tasks import get_task


def root_cause(err):
    while getattr(err, "exceptions", None):
        err = err.exceptions[0]
    return err


def one_line(text, limit=140):
    return " ".join(text.split())[:limit]


async def main(task_id, specs, scope):
    task = get_task(task_id)
    faults = [parse_fault(spec) for spec in specs]
    try:
        outcome = await run_once(
            task, faults, scope=scope, workdir=f".havoc/runs/{scope}-faulted"
        )
    except Exception as err:
        print(f"ERROR: {root_cause(err)}")
        return

    snap = outcome.snapshot
    print(f"task: {task.id}")
    print("faults planned:", ", ".join(f.id for f in faults) or "none")
    print()
    for record in outcome.trace:
        tags = " ".join(
            f"[{f['id']}{'' if f['applied'] else ' (not applicable)'}]" for f in record["faults"]
        )
        flag = "ERR" if record["is_error"] else "ok "
        response = record["response"]
        text = response["content"][0]["text"] if response.get("content") else str(response)
        print(f"  {record['seq']}. {flag} {record['tool']}({record['args']}) {tags}")
        print(f"       -> {one_line(text)}")

    missed = [f.id for f in faults if f.id not in outcome.fired]
    print()
    print("faults that fired:", ", ".join(sorted(outcome.fired)) or "none")
    print("faults that never fired:", ", ".join(missed) or "none")
    print()
    print("refunds :", [(r["order_id"], r["amount"]) for r in snap["refunds"]])
    print("emails  :", [(e["to_addr"], e["subject"]) for e in snap["emails"]])
    print("tickets :", [(t["id"], t["order_id"], t["status"]) for t in snap["tickets"]])
    print()
    print("run ended:", outcome.result.stop_reason, f"after {outcome.result.turns} turns")
    print("task check:", "PASS" if outcome.task_ok else "FAIL")
    if outcome.violations:
        for v in outcome.violations:
            print(f"VIOLATION {v.invariant}: {v.message}")
    else:
        print("violations: none")
    print("agent said:", one_line(outcome.result.answer, 300))


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

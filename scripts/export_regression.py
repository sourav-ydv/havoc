import argparse
import asyncio
import os

from havoc.faults import parse_fault
from havoc.regression import export_regression
from havoc.tasks import get_task


def root_cause(err):
    while getattr(err, "exceptions", None):
        err = err.exceptions[0]
    return err


async def main(args, scope):
    task = get_task(args.task)
    plan = [parse_fault(s) for s in args.fault]
    try:
        meta = await export_regression(
            task, plan, scope, f"regressions/{args.name}", args.name,
            f".havoc/runs/{scope}-export", notes=args.notes,
        )
    except Exception as err:
        print(f"ERROR: {root_cause(err)}")
        return
    print(f"saved regressions/{args.name}")
    print("task      :", meta["task"])
    print("failure   :", ", ".join(meta["signature"]))
    print("model     :", ", ".join(meta["models"]))
    print("recorded  :", meta["calls"], "model calls")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("--name", required=True)
    parser.add_argument("--fault", action="append", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--provider")
    parser.add_argument("--scope")
    args = parser.parse_args()
    if args.provider:
        os.environ["HAVOC_PROVIDERS"] = args.provider
    asyncio.run(main(args, args.scope or args.provider or "baseline"))

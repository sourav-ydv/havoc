import argparse
import asyncio
import os
import sys
from pathlib import Path

from havoc.regression import live_failure_rate, load_regression
from havoc.tasks import get_task


def root_cause(err):
    while getattr(err, "exceptions", None):
        err = err.exceptions[0]
    return err


async def main(args, scope):
    exit_code = 0
    for folder in args.cases:
        data = load_regression(folder)
        meta = data["meta"]
        task = get_task(meta["task"])
        try:
            hits, trials = await live_failure_rate(
                task, data["plan"], meta["signature"], args.trials, args.temperature,
                scope, f".havoc/runs/{scope}-gate",
            )
        except Exception as err:
            print(f"ERROR {Path(folder).name}: {root_cause(err)}")
            return 2
        rate = hits / trials
        verdict = "ok" if rate <= args.max_failure_rate else "FAILS"
        print(f"{verdict:<6} {meta['name']:<30} {hits}/{trials} runs show {', '.join(meta['signature'])}")
        if rate > args.max_failure_rate:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", nargs="+")
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-failure-rate", type=float, default=0.0)
    parser.add_argument("--provider")
    parser.add_argument("--scope")
    args = parser.parse_args()
    if args.provider:
        os.environ["HAVOC_PROVIDERS"] = args.provider
    sys.exit(asyncio.run(main(args, args.scope or args.provider or "baseline")))

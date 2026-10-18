import argparse
import asyncio
import functools
import os

from havoc.agent import VARIANTS
from havoc.faults import parse_fault
from havoc.runner import run_once
from havoc.shrink import reproduction_rate
from havoc.stats import fisher_exact, wilson
from havoc.tasks import get_task


def root_cause(err):
    while getattr(err, "exceptions", None):
        err = err.exceptions[0]
    return err


def cell(hits, trials):
    low, high = wilson(hits, trials)
    return f"{hits}/{trials} ({100 * hits / trials:.0f}%, {100 * low:.0f}-{100 * high:.0f}%)"


async def main(args, provider):
    task = get_task(args.task)
    plan = [parse_fault(s) for s in args.fault]
    target = frozenset(args.target.split(","))
    alone = plan[:1] if args.alone == "first" else plan if args.alone == "all" else []
    cases = [("the pair", plan)] + [(f"{f.id} alone", [f]) for f in alone]
    scopes = {"baseline": f"{provider}-t0.7", args.variant: f"{provider}-{args.variant}-t0.7"}
    results = {}

    try:
        for variant, scope in scopes.items():
            runner = functools.partial(run_once, system_prompt=VARIANTS[variant])
            for label, faults in cases:
                results[(variant, label)] = await reproduction_rate(
                    task, faults, target, scope, f".havoc/runs/{provider}-compare",
                    args.trials, 0.7, runner,
                )
    except Exception as err:
        print(f"ERROR: {root_cause(err)}")
        print("progress is saved in the cache, run the same command again to continue")
        return

    print(f"failure: {', '.join(sorted(target))}, {args.trials} fresh runs each at temperature 0.7\n")
    print(f"{'case':<46}{'baseline prompt':<26}{args.variant + ' prompt':<26}")
    for label, _ in cases:
        base = results[("baseline", label)]
        fixed = results[(args.variant, label)]
        print(f"{label:<46}{cell(*base):<26}{cell(*fixed):<26}")

    base_hits, n = results[("baseline", "the pair")]
    fixed_hits, _ = results[(args.variant, "the pair")]
    p = fisher_exact(base_hits, n - base_hits, fixed_hits, n - fixed_hits)
    print(f"\nthe pair, baseline against {args.variant}: Fisher exact p = {p:.3f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("--fault", action="append", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="sequential")
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--alone", choices=["first", "all", "none"], default="first")
    parser.add_argument("--provider", default="gemini")
    args = parser.parse_args()
    os.environ["HAVOC_PROVIDERS"] = args.provider
    asyncio.run(main(args, args.provider))

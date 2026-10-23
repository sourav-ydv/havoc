import argparse
import asyncio
import functools
import json
import os
import time
from pathlib import Path

from havoc import llm
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


def p_text(p):
    return "< 0.001" if p < 0.001 else f"{p:.3f}"


def cell(hits, trials):
    low, high = wilson(hits, trials)
    return f"{hits}/{trials} ({100 * hits / trials:.0f}%, {100 * low:.0f}-{100 * high:.0f}%)"


async def collect(args, provider, task, cases, target, scopes, results):
    for variant, scope in scopes.items():
        runner = functools.partial(run_once, system_prompt=VARIANTS[variant])
        for label, faults in cases:
            if (variant, label) in results:
                continue
            results[(variant, label)] = await reproduction_rate(
                task, faults, target, scope, f".havoc/runs/{provider}-compare",
                args.trials, 0.7, runner,
            )


async def main(args, provider):
    task = get_task(args.task)
    plan = [parse_fault(s) for s in args.fault]
    target = frozenset(args.target.split(","))
    alone = plan[:1] if args.alone == "first" else plan if args.alone == "all" else []
    cases = [("the pair", plan)] + [(f"{f.id} alone", [f]) for f in alone]
    scopes = {"baseline": f"{provider}-t0.7", args.variant: f"{provider}-{args.variant}-t0.7"}
    results = {}

    while True:
        try:
            await collect(args, provider, task, cases, target, scopes, results)
            break
        except Exception as err:
            cause = root_cause(err)
            print(f"ERROR: {cause}")
            delay = llm.quota_delay(cause)
            if not args.wait or delay is None:
                print("progress is saved in the cache, run the same command again to continue")
                return
            print(f"waiting about {delay / 3600:.1f} hours for the quota, leave this window open")
            await asyncio.sleep(delay + 120)
            print("trying again")

    print(f"failure: {', '.join(sorted(target))}, {args.trials} fresh runs each at temperature 0.7\n")
    print(f"{'case':<46}{'baseline prompt':<26}{args.variant + ' prompt':<26}")
    for label, _ in cases:
        base = results[("baseline", label)]
        fixed = results[(args.variant, label)]
        print(f"{label:<46}{cell(*base):<26}{cell(*fixed):<26}")

    print()
    for label, _ in cases:
        base_hits, n = results[("baseline", label)]
        fixed_hits, m = results[(args.variant, label)]
        p = fisher_exact(base_hits, n - base_hits, fixed_hits, m - fixed_hits)
        print(f"{label}: baseline against {args.variant}, Fisher exact p = {p_text(p)}")

    comparisons = []
    for label, _ in cases:
        base_hits, n = results[("baseline", label)]
        fixed_hits, m = results[(args.variant, label)]
        comparisons.append(
            {
                "case": label,
                "baseline_against_variant_p": fisher_exact(base_hits, n - base_hits, fixed_hits, m - fixed_hits),
            }
        )

    if args.out:
        record = {
            "task": args.task,
            "faults": [f.id for f in plan],
            "target": sorted(target),
            "variant": args.variant,
            "temperature": 0.7,
            "generated": time.strftime("%Y-%m-%d"),
            "results": [
                {
                    "case": label,
                    "baseline": dict(zip(("hits", "trials"), results[("baseline", label)])),
                    args.variant: dict(zip(("hits", "trials"), results[(args.variant, label)])),
                }
                for label, _ in cases
            ],
            "comparisons": comparisons,
        }
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(f"\nsaved {out}")

    if len(cases) > 1:
        pair_hits, n = results[("baseline", "the pair")]
        alone_hits, m = results[("baseline", cases[1][0])]
        p = fisher_exact(pair_hits, n - pair_hits, alone_hits, m - alone_hits)
        print(f"baseline, the pair against {cases[1][0]}: Fisher exact p = {p_text(p)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("--fault", action="append", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="sequential")
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--alone", choices=["first", "all", "none"], default="first")
    parser.add_argument("--provider", default="gemini")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args()
    os.environ["HAVOC_PROVIDERS"] = args.provider
    asyncio.run(main(args, args.provider))

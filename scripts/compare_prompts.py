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
    guard = getattr(args, "guard", None)
    for variant, scope in scopes.items():
        runner = functools.partial(run_once, system_prompt=VARIANTS[variant], guard=guard)
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
    guard = getattr(args, "guard", None)
    mark = f"-{guard}" if guard else ""
    scopes = {"baseline": f"{provider}{mark}-t0.7"}
    if args.arms == "both":
        scopes[args.variant] = f"{provider}-{args.variant}{mark}-t0.7"
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

    both = args.variant in scopes
    print(f"failure: {', '.join(sorted(target))}, {args.trials} fresh runs each at temperature 0.7\n")
    header = f"{'case':<46}{'baseline prompt':<26}"
    if both:
        header += f"{args.variant + ' prompt':<26}"
    print(header)
    for label, _ in cases:
        row = f"{label:<46}{cell(*results[('baseline', label)]):<26}"
        if both:
            row += f"{cell(*results[(args.variant, label)]):<26}"
        print(row)

    comparisons = []
    if both:
        print()
        for label, _ in cases:
            base_hits, n = results[("baseline", label)]
            fixed_hits, m = results[(args.variant, label)]
            p = fisher_exact(base_hits, n - base_hits, fixed_hits, m - fixed_hits)
            comparisons.append({"case": label, "baseline_against_variant_p": p})
            print(f"{label}: baseline against {args.variant}, Fisher exact p = {p_text(p)}")

    if args.out:
        rows = []
        for label, _ in cases:
            row = {"case": label, "baseline": dict(zip(("hits", "trials"), results[("baseline", label)]))}
            if both:
                row[args.variant] = dict(zip(("hits", "trials"), results[(args.variant, label)]))
            rows.append(row)
        record = {
            "task": args.task,
            "provider": provider,
            "faults": [f.id for f in plan],
            "target": sorted(target),
            "variant": args.variant if both else None,
            "guard": guard,
            "temperature": 0.7,
            "generated": time.strftime("%Y-%m-%d"),
            "results": rows,
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
        print(f"\nbaseline, the pair against {cases[1][0]}: Fisher exact p = {p_text(p)}")


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
    parser.add_argument("--arms", choices=["both", "baseline"], default="both")
    parser.add_argument("--guard", choices=["refund-claim"])
    args = parser.parse_args()
    os.environ["HAVOC_PROVIDERS"] = args.provider
    asyncio.run(main(args, args.provider))

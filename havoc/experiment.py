import hashlib
import json
import random
import time
from collections import defaultdict
from types import SimpleNamespace

from havoc.faults import Fault
from havoc.generate import fault_universe, retry_pairs, sample_plans
from havoc.runner import run_once
from havoc.shrink import investigate
from havoc.stats import fisher_exact, wilson
from havoc.tasks import get_task

STRATEGIES = ["single", "retry", "random2", "random3"]


def plan_key(plan):
    parts = sorted(json.dumps(f.to_dict(), sort_keys=True) for f in plan)
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def build_plans(strategy, universe, seed, count):
    if strategy == "single":
        plans = [[f] for f in universe]
        random.Random(seed).shuffle(plans)
    elif strategy == "retry":
        plans = retry_pairs(universe)
    elif strategy == "random2":
        plans = sample_plans(universe, 2, count, seed)
    elif strategy == "random3":
        plans = sample_plans(universe, 3, count, seed)
    else:
        raise ValueError(f"unknown strategy: {strategy}")
    return plans[:count]


def parse_budget(text):
    budget = {}
    for part in text.split(","):
        name, _, number = part.partition("=")
        if name.strip() not in STRATEGIES:
            raise ValueError(f"unknown strategy: {name}")
        budget[name.strip()] = int(number)
    return budget


async def run_discovery(task_ids, budget, scope, seed, store, workdir, kinds=None, runner=run_once, log=print):
    done = {
        (r["task"], r["strategy"], r["key"])
        for r in store.load()
        if r.get("type") == "run"
    }

    for task_id in task_ids:
        task = get_task(task_id)
        clean = await runner(task, scope=scope, workdir=workdir, tag=f"clean-{task_id}")
        if clean.failed:
            store.append({"type": "clean_failed", "task": task_id, "signature": sorted(clean.signature)})
            log(f"{task_id}: the clean run already fails, skipping this task")
            continue

        universe = fault_universe(clean.trace, kinds)
        log(f"{task_id}: {len(universe)} candidate faults")

        for strategy in STRATEGIES:
            if strategy not in budget:
                continue
            plans = build_plans(strategy, universe, seed, budget[strategy])
            fresh = 0
            for plan in plans:
                key = plan_key(plan)
                if (task_id, strategy, key) in done:
                    continue
                outcome = await runner(task, plan, scope=scope, workdir=workdir, tag="run")
                store.append(
                    {
                        "type": "run",
                        "task": task_id,
                        "strategy": strategy,
                        "size": len(plan),
                        "key": key,
                        "plan": [f.to_dict() for f in plan],
                        "failed": bool(outcome.failed),
                        "signature": sorted(outcome.signature),
                        "task_ok": bool(outcome.task_ok),
                        "violations": [f"{v.invariant}: {v.message}" for v in outcome.violations],
                        "fired": sorted(outcome.fired),
                        "stop": outcome.result.stop_reason,
                        "turns": outcome.result.turns,
                        "tool_calls": len(outcome.trace),
                        "models": list(outcome.result.models),
                        "time": time.time(),
                    }
                )
                done.add((task_id, strategy, key))
                fresh += 1
                mark = "FAIL" if outcome.failed else "ok  "
                log(f"  [{strategy}] {mark} {' + '.join(f.id for f in plan)}")
            log(f"{task_id} {strategy}: {fresh} new runs, {len(plans) - fresh} already done")


async def run_analysis(task_ids, scope, runs_store, analysis_store, trials, workdir, runner=run_once, log=print):
    runs = [r for r in runs_store.load() if r.get("type") == "run"]
    analyzed = {(a["task"], a["key"]) for a in analysis_store.load()}

    alone = defaultdict(list)
    for r in runs:
        if r["strategy"] == "single" and r["failed"]:
            fault = Fault.from_dict(r["plan"][0])
            alone[(r["task"], fault.id)].append(frozenset(r["signature"]))

    for r in runs:
        if r["strategy"] == "single" or not r["failed"] or r["task"] not in task_ids:
            continue
        if (r["task"], r["key"]) in analyzed:
            continue

        plan = [Fault.from_dict(d) for d in r["plan"]]
        signature = frozenset(r["signature"])
        explaining = [
            f.id
            for f in plan
            if any(signature <= s for s in alone.get((r["task"], f.id), []))
        ]
        analyzed.add((r["task"], r["key"]))

        if explaining:
            analysis_store.append(
                {"type": "explained", "task": r["task"], "key": r["key"], "plan": r["plan"], "by": explaining}
            )
            log(f"  explained by a single fault: {' + '.join(f.id for f in plan)}")
            continue

        task = get_task(r["task"])
        result, check = await investigate(
            task, plan, SimpleNamespace(signature=signature), scope, workdir, trials, runner
        )
        record = {
            "type": "analysis",
            "task": r["task"],
            "key": r["key"],
            "plan": r["plan"],
            "signature": sorted(signature),
            "reproduced_when_shrinking": result.reproduced,
            "fails_without_faults": result.fails_without_faults,
            "minimal": [f.to_dict() for f in result.minimal],
            "shrink_runs": result.runs,
            "status": check["status"] if check else "unconfirmed",
            "pure_compound": bool(check and check["pure_compound"]),
            "rates": {str(t): list(v) for t, v in check["rates"].items()} if check else {},
            "singles": (
                {fid: {str(t): list(v) for t, v in per.items()} for fid, per in check["singles"].items()}
                if check
                else {}
            ),
        }
        analysis_store.append(record)
        state = record["status"] + (", pure compound" if record["pure_compound"] else "")
        log(f"  analysed {' + '.join(f.id for f in plan)} -> {len(result.minimal)} faults, {state}")


def rate_cell(hits, total):
    if total == 0:
        return "-"
    low, high = wilson(hits, total)
    return f"{hits}/{total} ({100 * hits / total:.0f}%, {100 * low:.0f}-{100 * high:.0f}%)"


def summarize(runs, analyses):
    runs = [r for r in runs if r.get("type") == "run"]
    by_strategy = defaultdict(lambda: [0, 0])
    by_cell = defaultdict(lambda: [0, 0])
    for r in runs:
        by_strategy[r["strategy"]][0] += 1
        by_strategy[r["strategy"]][1] += r["failed"]
        by_cell[(r["task"], r["strategy"])][0] += 1
        by_cell[(r["task"], r["strategy"])][1] += r["failed"]

    single_runs, single_failed = by_strategy["single"]
    compound_runs = sum(v[0] for k, v in by_strategy.items() if k != "single")
    compound_failed = sum(v[1] for k, v in by_strategy.items() if k != "single")
    p_value = None
    if single_runs and compound_runs:
        p_value = fisher_exact(
            single_failed, single_runs - single_failed,
            compound_failed, compound_runs - compound_failed,
        )

    single_causes = {}
    for r in runs:
        if r["strategy"] == "single" and r["failed"]:
            fault = Fault.from_dict(r["plan"][0])
            single_causes[(r["task"], fault.id)] = r["signature"]

    explained = [a for a in analyses if a["type"] == "explained"]
    studied = [a for a in analyses if a["type"] == "analysis"]
    pure = {}
    for a in studied:
        if a["pure_compound"] and a["status"] != "unconfirmed":
            ids = tuple(sorted(Fault.from_dict(d).id for d in a["minimal"]))
            pure.setdefault((a["task"], ids), a)

    handled = {(a["task"], a["key"]) for a in analyses}
    pending = {
        (r["task"], r["key"])
        for r in runs
        if r["strategy"] != "single" and r["failed"] and (r["task"], r["key"]) not in handled
    }

    return {
        "by_strategy": dict(by_strategy),
        "by_cell": dict(by_cell),
        "single": (single_runs, single_failed),
        "compound": (compound_runs, compound_failed),
        "p_value": p_value,
        "single_causes": single_causes,
        "explained": len(explained),
        "studied": studied,
        "pure_compound": pure,
        "pending": len(pending),
    }


def render_markdown(summary):
    lines = ["# Experiment results", ""]

    lines += ["## Failures found per strategy", "", "| Strategy | Runs | Runs that failed |", "|---|---|---|"]
    for name in STRATEGIES:
        if name in summary["by_strategy"]:
            runs, failed = summary["by_strategy"][name]
            lines.append(f"| {name} | {runs} | {rate_cell(failed, runs)} |")
    lines.append("")

    tasks = sorted({task for task, _ in summary["by_cell"]})
    if tasks:
        lines += ["## By task", "", "| Task | " + " | ".join(s for s in STRATEGIES if s in summary["by_strategy"]) + " |"]
        lines.append("|---|" + "---|" * len([s for s in STRATEGIES if s in summary["by_strategy"]]))
        for task in tasks:
            cells = []
            for name in STRATEGIES:
                if name in summary["by_strategy"]:
                    runs, failed = summary["by_cell"].get((task, name), [0, 0])
                    cells.append(f"{failed}/{runs}")
            lines.append(f"| {task} | " + " | ".join(cells) + " |")
        lines.append("")

    sr, sf = summary["single"]
    cr, cf = summary["compound"]
    lines += ["## Single faults against combinations", ""]
    lines.append(f"- single faults: {rate_cell(sf, sr)}")
    lines.append(f"- combinations: {rate_cell(cf, cr)}")
    if summary["p_value"] is not None:
        lines.append(f"- Fisher exact test on the two failure rates: p = {summary['p_value']:.3f}")
    lines.append("")

    lines += ["## What the failures turned out to be", ""]
    lines.append(f"- distinct single faults that broke the agent: {len(summary['single_causes'])}")
    lines.append(f"- failing combinations already explained by one of their faults: {summary['explained']}")
    lines.append(f"- failing combinations studied (shrunk and re-run): {len(summary['studied'])}")
    lines.append(f"- failing combinations not studied yet: {summary['pending']}")
    lines.append(f"- pure compound failures (no fault of the pair fails alone): {len(summary['pure_compound'])}")
    lines.append("")

    for (task, ids), a in sorted(summary["pure_compound"].items()):
        rates = ", ".join(f"temp {t}: {h}/{n}" for t, (h, n) in a["rates"].items())
        lines.append(f"### {task}: {' + '.join(ids)}")
        lines.append(f"- breaks: {', '.join(a['signature'])}")
        lines.append(f"- fails again in fresh runs: {rates}")
        lines.append(f"- status: {a['status']}")
        for fid, per in a["singles"].items():
            alone = ", ".join(f"temp {t}: {h}/{n}" for t, (h, n) in per.items())
            lines.append(f"- {fid} alone: {alone}")
        lines.append("")

    if summary["single_causes"]:
        lines += ["### Single-fault failures", ""]
        for (task, fault_id), signature in sorted(summary["single_causes"].items()):
            lines.append(f"- {task}: {fault_id} -> {', '.join(signature)}")
        lines.append("")

    return "\n".join(lines)

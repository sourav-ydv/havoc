import argparse
import asyncio
import json
import os
from pathlib import Path

from havoc import llm
from havoc.experiment import (
    parse_budget,
    render_markdown,
    run_analysis,
    run_discovery,
    summarize,
)
from havoc.store import JsonlStore
from havoc.tasks import TASKS

DEFAULT_BUDGET = "single=45,retry=12,random2=30"


def root_cause(err):
    while getattr(err, "exceptions", None):
        err = err.exceptions[0]
    return err


def locations(name):
    root = Path(".havoc/experiments") / name
    return {
        "runs": JsonlStore(root / "runs.jsonl"),
        "analysis": JsonlStore(root / "analysis.jsonl"),
        "report": root / "results.md",
        "config": root / "config.json",
    }


def task_list(text):
    return text.split(",") if text else [t.id for t in TASKS]


async def do_run(args, scope, where):
    budget = parse_budget(args.budget)
    kinds = set(args.kinds.split(",")) if args.kinds else None
    tasks = task_list(args.tasks)
    where["config"].parent.mkdir(parents=True, exist_ok=True)
    where["config"].write_text(
        json.dumps(
            {"name": args.name, "scope": scope, "tasks": tasks, "budget": budget,
             "seed": args.seed, "kinds": sorted(kinds) if kinds else None},
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        await run_discovery(
            tasks, budget, scope, args.seed, where["runs"],
            f".havoc/runs/{scope}-exp-{args.name}", kinds,
        )
    except Exception as err:
        print(f"\nstopped early: {root_cause(err)}")
        print("everything so far is saved, run the same command again to continue")
    print(llm.stats, llm.provider_counts)


async def do_analyze(args, scope, where):
    try:
        await run_analysis(
            task_list(args.tasks), scope, where["runs"], where["analysis"],
            args.trials, f".havoc/runs/{scope}-exp-{args.name}",
        )
    except Exception as err:
        print(f"\nstopped early: {root_cause(err)}")
        print("everything so far is saved, run the same command again to continue")
    print(llm.stats, llm.provider_counts)


def do_report(args, where):
    summary = summarize(where["runs"].load(), where["analysis"].load())
    text = render_markdown(summary)
    where["report"].write_text(text, encoding="utf-8")
    print(text)
    print(f"written to {where['report']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "analyze", "report"])
    parser.add_argument("--name", required=True)
    parser.add_argument("--provider")
    parser.add_argument("--tasks")
    parser.add_argument("--budget", default=DEFAULT_BUDGET)
    parser.add_argument("--kinds")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--trials", type=int, default=5)
    args = parser.parse_args()

    if args.provider:
        os.environ["HAVOC_PROVIDERS"] = args.provider
    scope = args.provider or "baseline"
    where = locations(args.name)

    if args.command == "run":
        asyncio.run(do_run(args, scope, where))
    elif args.command == "analyze":
        asyncio.run(do_analyze(args, scope, where))
    else:
        do_report(args, where)

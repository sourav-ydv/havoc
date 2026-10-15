import json
import time
from pathlib import Path

from havoc import llm
from havoc.cache import Cache
from havoc.faults import Fault
from havoc.runner import run_once
from havoc.tasks import get_task


def save_regression(directory, meta, plan, entries):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (directory / "plan.json").write_text(
        json.dumps([f.to_dict() for f in plan], indent=2), encoding="utf-8"
    )
    (directory / "cassette.json").write_text(json.dumps(entries), encoding="utf-8")


def load_regression(directory):
    directory = Path(directory)
    return {
        "meta": json.loads((directory / "meta.json").read_text(encoding="utf-8")),
        "plan": [
            Fault.from_dict(d)
            for d in json.loads((directory / "plan.json").read_text(encoding="utf-8"))
        ],
        "entries": json.loads((directory / "cassette.json").read_text(encoding="utf-8")),
    }


async def export_regression(
    task, plan, scope, directory, name, workdir, notes="", extra=None, runner=run_once
):
    with llm.record_calls() as entries:
        outcome = await runner(task, plan, scope=scope, workdir=workdir, tag="export")

    if not outcome.failed:
        raise ValueError(
            "this plan did not fail when replayed, so there is nothing to record. "
            "Use the scope and plan of a failure that is in the cache."
        )

    meta = {
        "name": name,
        "task": task.id,
        "scope": scope,
        "temperature": 0.0,
        "signature": sorted(outcome.signature),
        "violations": [f"{v.invariant}: {v.message}" for v in outcome.violations],
        "models": list(outcome.result.models),
        "calls": len(entries),
        "recorded": time.strftime("%Y-%m-%d"),
        "notes": notes,
    }
    meta.update(extra or {})
    save_regression(directory, meta, plan, entries)
    return meta


async def replay_regression(directory, workdir, tmp, runner=run_once):
    data = load_regression(directory)
    cache = Cache(Path(tmp) / "cassette.db")
    for entry in data["entries"]:
        body = entry["body"]
        cache.put(entry["key"], "cassette", body.get("model", "cassette"), body)

    task = get_task(data["meta"]["task"])
    with llm.replay_from(cache):
        outcome = await runner(
            task,
            data["plan"],
            scope=data["meta"]["scope"],
            workdir=workdir,
            tag="replay",
            temperature=data["meta"].get("temperature", 0.0),
        )
    cache.close()
    return data, outcome


async def live_failure_rate(task, plan, signature, trials, temperature, scope, workdir, runner=run_once):
    target = frozenset(signature)
    hits = 0
    for i in range(trials):
        outcome = await runner(
            task, list(plan), scope=f"{scope}-gate{i}", workdir=workdir,
            tag=f"gate{i}", temperature=temperature,
        )
        hits += target <= outcome.signature
    return hits, trials

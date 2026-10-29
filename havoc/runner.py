from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from havoc.agent import RunResult, run_agent
from havoc.invariants import RunContext, Violation, check_all
from havoc.proxy.intercept import load_trace
from havoc.sandbox import state
from havoc.sandbox.session import open_session


@dataclass
class Outcome:
    task_id: str
    task_ok: bool
    violations: list
    result: RunResult
    trace: list
    snapshot: dict
    faults: list = field(default_factory=list)

    @property
    def failed(self):
        return (not self.task_ok) or bool(self.violations)

    @property
    def signature(self):
        names = {v.invariant for v in self.violations}
        if not self.task_ok:
            names.add("task_check")
        return frozenset(names)

    @property
    def fired(self):
        return {f["id"] for record in self.trace for f in record["faults"]}


async def run_once(
    task, faults=(), scope="baseline", workdir=".havoc/runs", tag=None, temperature=0.0,
    system_prompt=None, guard=None,
):
    name = tag or task.id
    db_path = Path(workdir) / f"{name}.db"
    trace_path = Path(workdir) / f"{name}.jsonl"
    plan = list(faults)

    state.reset(db_path)
    async with open_session(db_path, trace_path, plan or None, guard=guard) as session:
        extra = {"system_prompt": system_prompt} if system_prompt else {}
        result = await run_agent(
            session, task.prompt, scope=scope, temperature=temperature, **extra
        )

    with closing(state.connect(db_path)) as db:
        snapshot = state.snapshot(db)
    trace = load_trace(trace_path)

    context = RunContext(snapshot, trace, result.answer, result.stop_reason, result.turns)
    return Outcome(
        task.id,
        task.check(snapshot),
        check_all(context),
        result,
        trace,
        snapshot,
        plan,
    )

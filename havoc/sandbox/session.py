import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from havoc.faults import save_plan


@asynccontextmanager
async def open_session(db_path, trace_path=None, faults=None, guard=None):
    command = [sys.executable, "-m", "havoc.sandbox.server"]

    if faults and not trace_path:
        raise ValueError("a trace path is needed when faults are given")

    if trace_path:
        proxy = [sys.executable, "-m", "havoc.proxy", "--trace", str(trace_path)]
        if faults:
            plan_path = Path(trace_path).with_suffix(".faults.json")
            save_plan(plan_path, faults)
            proxy += ["--faults", str(plan_path)]
        if guard:
            proxy += ["--guard", guard]
        command = proxy + ["--"] + command

    params = StdioServerParameters(
        command=command[0],
        args=command[1:],
        env={**os.environ, "HAVOC_STATE_DB": str(db_path)},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session
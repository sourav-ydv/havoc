import os
import sys
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@asynccontextmanager
async def open_session(db_path, trace_path=None):
    command = [sys.executable, "-m", "havoc.sandbox.server"]
    if trace_path:
        command = [
            sys.executable,
            "-m",
            "havoc.proxy",
            "--trace",
            str(trace_path),
            "--",
        ] + command

    params = StdioServerParameters(
        command=command[0],
        args=command[1:],
        env={**os.environ, "HAVOC_STATE_DB": str(db_path)},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session

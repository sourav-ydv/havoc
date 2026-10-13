import os
import sys
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@asynccontextmanager
async def open_session(db_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "havoc.sandbox.server"],
        env={**os.environ, "HAVOC_STATE_DB": str(db_path)},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session

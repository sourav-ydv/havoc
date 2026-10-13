import asyncio
import json
import os
import sys
from contextlib import closing

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from havoc.sandbox import state

DB = ".havoc/demo_state.db"


async def main():
    state.reset(DB)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "havoc.sandbox.server"],
        env={**os.environ, "HAVOC_STATE_DB": DB},
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            listing = await session.list_tools()
            for tool in listing.tools:
                print(f"{tool.name}: {tool.description}")

            calls = [
                ("get_order", {"order_id": "A1001"}),
                ("issue_refund", {"order_id": "A1001", "amount": 79.99, "reason": "damaged"}),
                ("issue_refund", {"order_id": "A1001", "amount": 10}),
                ("send_email", {"to_addr": "maya@example.com", "subject": "Refund", "body": "Done."}),
            ]
            for name, args in calls:
                result = await session.call_tool(name, args)
                print(f"\n{name} {args}")
                print("  error:", result.is_error)
                print("  text :", result.content[0].text)

    with closing(state.connect(DB)) as db:
        print("\nfinal state")
        print(json.dumps(state.snapshot(db), indent=2))


asyncio.run(main())

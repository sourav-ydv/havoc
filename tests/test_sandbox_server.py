import asyncio
import json
import os
import sys
from contextlib import closing

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from havoc.sandbox import state


async def run_calls(db_path, calls):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "havoc.sandbox.server"],
        env={**os.environ, "HAVOC_STATE_DB": str(db_path)},
    )
    outcomes = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listing = await session.list_tools()
            for name, args in calls:
                outcomes.append(await session.call_tool(name, args))
    return [t.name for t in listing.tools], outcomes


def test_server_exposes_expected_tools(tmp_path):
    db_path = tmp_path / "s.db"
    state.reset(db_path)
    names, _ = asyncio.run(run_calls(db_path, []))
    assert set(names) == {
        "get_order",
        "issue_refund",
        "send_email",
        "create_ticket",
        "close_ticket",
    }


def test_refund_through_server_changes_state(tmp_path):
    db_path = tmp_path / "s.db"
    state.reset(db_path)
    _, outcomes = asyncio.run(
        run_calls(db_path, [("issue_refund", {"order_id": "A1001", "amount": 20})])
    )
    assert not outcomes[0].is_error
    assert json.loads(outcomes[0].content[0].text)["amount"] == 20
    with closing(state.connect(db_path)) as db:
        assert len(state.snapshot(db)["refunds"]) == 1


def test_rejected_refund_comes_back_as_error(tmp_path):
    db_path = tmp_path / "s.db"
    state.reset(db_path)
    _, outcomes = asyncio.run(
        run_calls(db_path, [("issue_refund", {"order_id": "A1005", "amount": 10})])
    )
    assert outcomes[0].is_error
    with closing(state.connect(db_path)) as db:
        assert state.snapshot(db)["refunds"] == []


def test_error_message_reaches_the_client(tmp_path):
    db_path = tmp_path / "s.db"
    state.reset(db_path)
    _, outcomes = asyncio.run(
        run_calls(db_path, [("issue_refund", {"order_id": "A1005", "amount": 10})])
    )
    assert "cancelled" in outcomes[0].content[0].text

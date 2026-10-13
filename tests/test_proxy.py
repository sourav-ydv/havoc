import asyncio
import json
from contextlib import closing

from havoc.proxy.intercept import load_trace
from havoc.sandbox import state
from havoc.sandbox.session import open_session


def run(db_path, trace_path, calls):
    async def go():
        async with open_session(db_path, trace_path) as session:
            tools = await session.list_tools()
            results = [await session.call_tool(n, a) for n, a in calls]
            return tools, results

    return asyncio.run(go())


def test_tool_listing_works_through_the_proxy(tmp_path):
    state.reset(tmp_path / "s.db")
    tools, _ = run(tmp_path / "s.db", tmp_path / "t.jsonl", [])
    assert len(tools.tools) == 5


def test_calls_through_the_proxy_are_traced(tmp_path):
    db_path, trace = tmp_path / "s.db", tmp_path / "t.jsonl"
    state.reset(db_path)
    _, results = run(
        db_path,
        trace,
        [
            ("get_order", {"order_id": "A1001"}),
            ("issue_refund", {"order_id": "A1005", "amount": 10}),
        ],
    )
    records = load_trace(trace)
    assert [r["tool"] for r in records] == ["get_order", "issue_refund"]
    assert records[0]["is_error"] is False
    assert records[1]["is_error"] is True
    assert "cancelled" in json.dumps(records[1]["response"])
    assert results[1].is_error


def test_the_agent_sees_the_same_result_as_the_server_sent(tmp_path):
    db_path, trace = tmp_path / "s.db", tmp_path / "t.jsonl"
    state.reset(db_path)
    _, results = run(db_path, trace, [("get_order", {"order_id": "A1002"})])
    seen = json.loads(results[0].content[0].text)
    traced = json.loads(load_trace(trace)[0]["response"]["content"][0]["text"])
    assert seen == traced
    assert seen["item"] == "Phone case"


def test_side_effects_still_reach_the_database(tmp_path):
    db_path, trace = tmp_path / "s.db", tmp_path / "t.jsonl"
    state.reset(db_path)
    run(db_path, trace, [("issue_refund", {"order_id": "A1002", "amount": 12.5})])
    with closing(state.connect(db_path)) as db:
        assert len(state.snapshot(db)["refunds"]) == 1


def test_a_second_session_starts_a_fresh_trace(tmp_path):
    db_path, trace = tmp_path / "s.db", tmp_path / "t.jsonl"
    state.reset(db_path)
    run(db_path, trace, [("get_order", {"order_id": "A1001"})])
    run(db_path, trace, [("get_order", {"order_id": "A1002"}), ("get_order", {"order_id": "A1003"})])
    assert len(load_trace(trace)) == 2

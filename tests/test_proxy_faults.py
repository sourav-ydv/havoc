import asyncio
import json
from contextlib import closing

from havoc.faults import make
from havoc.proxy.intercept import load_trace
from havoc.sandbox import state
from havoc.sandbox.session import open_session


def run(tmp_path, faults, calls):
    db_path, trace = tmp_path / "s.db", tmp_path / "t.jsonl"
    state.reset(db_path)

    async def go():
        async with open_session(db_path, trace, faults) as session:
            return [await session.call_tool(n, a) for n, a in calls]

    results = asyncio.run(go())
    with closing(state.connect(db_path)) as db:
        snap = state.snapshot(db)
    return results, snap, load_trace(trace)


def text(result):
    return result.content[0].text


def test_fail_before_means_the_action_never_happened(tmp_path):
    results, snap, _ = run(
        tmp_path,
        [make("issue_refund", 0, "fail_before")],
        [("issue_refund", {"order_id": "A1002", "amount": 12.5})],
    )
    assert results[0].is_error
    assert snap["refunds"] == []


def test_fail_after_means_it_happened_but_the_agent_thinks_it_did_not(tmp_path):
    results, snap, trace = run(
        tmp_path,
        [make("issue_refund", 0, "fail_after")],
        [("issue_refund", {"order_id": "A1002", "amount": 12.5})],
    )
    assert results[0].is_error
    assert "timed out" in text(results[0])
    assert len(snap["refunds"]) == 1
    assert "refund_id" in json.dumps(trace[0]["original"])


def test_retrying_after_an_ambiguous_failure_is_refused_by_the_tool(tmp_path):
    results, snap, _ = run(
        tmp_path,
        [make("issue_refund", 0, "fail_after")],
        [
            ("issue_refund", {"order_id": "A1002", "amount": 12.5}),
            ("issue_refund", {"order_id": "A1002", "amount": 12.5}),
        ],
    )
    assert results[0].is_error and results[1].is_error
    assert "exceed" in text(results[1])
    assert len(snap["refunds"]) == 1


def test_corrupted_data_reaches_the_agent_but_not_the_database(tmp_path):
    fault = make("get_order", 0, "corrupt_field", field="total", value=45.0)
    results, snap, _ = run(tmp_path, [fault], [("get_order", {"order_id": "A1003"})])
    assert json.loads(text(results[0]))["total"] == 45.0
    assert snap["orders"][2]["total"] == 349.0


def test_injection_arrives_inside_a_tool_result(tmp_path):
    fault = make("get_order", 0, "injection", name="approve_all")
    results, _, _ = run(tmp_path, [fault], [("get_order", {"order_id": "A1001"})])
    assert "pre-approved" in text(results[0])


def test_only_the_targeted_call_is_faulted(tmp_path):
    results, _, trace = run(
        tmp_path,
        [make("get_order", 1, "malformed")],
        [("get_order", {"order_id": "A1001"}), ("get_order", {"order_id": "A1002"})],
    )
    json.loads(text(results[0]))
    try:
        json.loads(text(results[1]))
        broken = False
    except ValueError:
        broken = True
    assert broken
    assert trace[0]["faults"] == []
    assert trace[1]["faults"][0]["applied"] is True


def test_compound_plan_across_two_calls(tmp_path):
    plan = [
        make("get_order", 0, "corrupt_field", field="status", value="shipped"),
        make("send_email", 0, "fail_before"),
    ]
    results, snap, trace = run(
        tmp_path,
        plan,
        [
            ("get_order", {"order_id": "A1001"}),
            ("send_email", {"to_addr": "maya@example.com", "subject": "s", "body": "b"}),
        ],
    )
    assert json.loads(text(results[0]))["status"] == "shipped"
    assert results[1].is_error
    assert snap["emails"] == []
    assert [len(r["faults"]) for r in trace] == [1, 1]

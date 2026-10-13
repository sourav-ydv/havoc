import json

import pytest

from havoc.faults import make
from havoc.proxy.intercept import Interceptor, TraceWriter, load_trace


def line(payload):
    return (json.dumps(payload) + "\n").encode()


def request(msg_id, name, args=None):
    return line(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        }
    )


def response(msg_id, payload):
    text = json.dumps(payload)
    return line(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"content": [{"type": "text", "text": text}], "isError": False},
        }
    )


def build(tmp_path, faults):
    sent = []
    interceptor = Interceptor(TraceWriter(tmp_path / "t.jsonl"), faults, emit=sent.append)
    return interceptor, sent, tmp_path / "t.jsonl"


def seen_text(raw):
    return json.loads(raw)["result"]["content"][0]["text"]


def test_fail_before_never_reaches_the_server(tmp_path):
    interceptor, sent, path = build(tmp_path, [make("issue_refund", 0, "fail_before")])
    forwarded = interceptor.from_client(request(7, "issue_refund", {"amount": 5}))
    assert forwarded is None
    reply = json.loads(sent[0])
    assert reply["id"] == 7
    assert reply["result"]["isError"] is True
    record = load_trace(path)[0]
    assert record["forwarded"] is False
    assert record["faults"] == [{"id": "issue_refund#0:fail_before", "applied": True}]


def test_nth_targets_only_that_occurrence(tmp_path):
    interceptor, sent, path = build(tmp_path, [make("get_order", 1, "fail_before")])
    first = request(1, "get_order")
    assert interceptor.from_client(first) == first
    interceptor.from_server(response(1, {"ok": 1}))
    assert interceptor.from_client(request(2, "get_order")) is None
    assert len(sent) == 1


def test_other_tools_are_not_touched(tmp_path):
    interceptor, sent, _ = build(tmp_path, [make("issue_refund", 0, "fail_before")])
    raw = request(1, "get_order")
    assert interceptor.from_client(raw) == raw
    assert sent == []


def test_counts_are_per_tool(tmp_path):
    interceptor, _, _ = build(tmp_path, [make("send_email", 0, "fail_before")])
    interceptor.from_client(request(1, "get_order"))
    interceptor.from_server(response(1, {}))
    interceptor.from_client(request(2, "get_order"))
    interceptor.from_server(response(2, {}))
    assert interceptor.from_client(request(3, "send_email")) is None


def test_response_fault_rewrites_what_the_agent_sees(tmp_path):
    fault = make("get_order", 0, "corrupt_field", field="total", value=45.0)
    interceptor, _, path = build(tmp_path, [fault])
    interceptor.from_client(request(1, "get_order"))
    out = interceptor.from_server(response(1, {"id": "A1003", "total": 349.0}))
    assert json.loads(seen_text(out))["total"] == 45.0
    record = load_trace(path)[0]
    assert record["forwarded"] is True
    assert json.loads(record["original"]["content"][0]["text"])["total"] == 349.0
    assert json.loads(record["response"]["content"][0]["text"])["total"] == 45.0


def test_unfaulted_response_is_passed_through_byte_for_byte(tmp_path):
    interceptor, _, path = build(tmp_path, [make("issue_refund", 0, "malformed")])
    interceptor.from_client(request(1, "get_order"))
    raw = response(1, {"a": 1})
    assert interceptor.from_server(raw) == raw
    assert "original" not in load_trace(path)[0]


def test_not_applicable_fault_leaves_the_bytes_alone(tmp_path):
    fault = make("get_order", 0, "drop_field", field="missing")
    interceptor, _, path = build(tmp_path, [fault])
    interceptor.from_client(request(1, "get_order"))
    raw = response(1, {"a": 1})
    assert interceptor.from_server(raw) == raw
    assert load_trace(path)[0]["faults"][0]["applied"] is False


def test_no_faults_behaves_like_before(tmp_path):
    interceptor, sent, path = build(tmp_path, [])
    raw = request(1, "get_order")
    assert interceptor.from_client(raw) == raw
    resp = response(1, {"a": 1})
    assert interceptor.from_server(resp) == resp
    assert load_trace(path)[0]["faults"] == []
    assert sent == []

import json

import pytest

from havoc.proxy.intercept import Interceptor, TraceWriter, load_trace


def line(payload):
    return (json.dumps(payload) + "\n").encode()


def call_request(msg_id, name, args):
    return line(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )


def ok_response(msg_id, text):
    return line(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"content": [{"type": "text", "text": text}], "isError": False},
        }
    )


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "trace.jsonl"
    return Interceptor(TraceWriter(path)), path


def test_a_call_and_its_response_become_one_record(setup):
    interceptor, path = setup
    interceptor.from_client(call_request(1, "get_order", {"order_id": "A1001"}))
    interceptor.from_server(ok_response(1, "found it"))
    records = load_trace(path)
    assert len(records) == 1
    assert records[0]["tool"] == "get_order"
    assert records[0]["args"] == {"order_id": "A1001"}
    assert records[0]["is_error"] is False
    assert records[0]["response"]["content"][0]["text"] == "found it"


def test_messages_pass_through_unchanged(setup):
    interceptor, _ = setup
    request = call_request(1, "get_order", {})
    response = ok_response(1, "x")
    assert interceptor.from_client(request) == request
    assert interceptor.from_server(response) == response


def test_non_tool_messages_are_not_recorded(setup):
    interceptor, path = setup
    interceptor.from_client(line({"jsonrpc": "2.0", "id": 5, "method": "tools/list"}))
    interceptor.from_server(line({"jsonrpc": "2.0", "id": 5, "result": {"tools": []}}))
    assert load_trace(path) == []


def test_tool_level_error_is_flagged(setup):
    interceptor, path = setup
    interceptor.from_client(call_request(2, "issue_refund", {}))
    interceptor.from_server(
        line(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"content": [{"type": "text", "text": "nope"}], "isError": True},
            }
        )
    )
    assert load_trace(path)[0]["is_error"] is True


def test_protocol_level_error_is_flagged(setup):
    interceptor, path = setup
    interceptor.from_client(call_request(3, "x", {}))
    interceptor.from_server(
        line({"jsonrpc": "2.0", "id": 3, "error": {"code": -32601, "message": "gone"}})
    )
    record = load_trace(path)[0]
    assert record["is_error"] is True
    assert record["response"]["message"] == "gone"


def test_garbage_lines_are_forwarded_untouched(setup):
    interceptor, path = setup
    assert interceptor.from_client(b"not json at all\n") == b"not json at all\n"
    assert interceptor.from_server(b"{broken\n") == b"{broken\n"
    assert load_trace(path) == []


def test_responses_are_matched_by_id_even_out_of_order(setup):
    interceptor, path = setup
    interceptor.from_client(call_request(1, "first", {}))
    interceptor.from_client(call_request(2, "second", {}))
    interceptor.from_server(ok_response(2, "b"))
    interceptor.from_server(ok_response(1, "a"))
    records = load_trace(path)
    assert [r["tool"] for r in records] == ["first", "second"]
    assert records[0]["response"]["content"][0]["text"] == "a"


def test_string_ids_work(setup):
    interceptor, path = setup
    interceptor.from_client(call_request("abc", "get_order", {}))
    interceptor.from_server(ok_response("abc", "x"))
    assert len(load_trace(path)) == 1


def test_response_with_unknown_id_is_ignored(setup):
    interceptor, path = setup
    interceptor.from_server(ok_response(99, "stray"))
    assert load_trace(path) == []


def test_missing_trace_file_loads_as_empty(tmp_path):
    assert load_trace(tmp_path / "nope.jsonl") == []


def test_writer_with_no_path_is_a_noop():
    TraceWriter(None).write({"anything": 1})

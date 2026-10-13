import asyncio
import json
from contextlib import closing
from types import SimpleNamespace

import pytest
from openai.types.chat import ChatCompletion

from havoc import agent
from havoc.agent import clean_schema, run_agent, to_openai_tools
from havoc.sandbox import state
from havoc.sandbox.session import open_session
from havoc.tasks import get_task


def reply(content=None, calls=None):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": args if isinstance(args, str) else json.dumps(args),
                },
            }
            for call_id, name, args in calls
        ]
    return ChatCompletion.model_validate(
        {
            "id": "t",
            "object": "chat.completion",
            "created": 0,
            "model": "fake",
            "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
        }
    )


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def __call__(self, messages, tools=None, temperature=0.0, scope=""):
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0)


def play(monkeypatch, tmp_path, replies, prompt="help the customer", max_turns=10):
    script = ScriptedLLM(replies)
    monkeypatch.setattr(agent.llm, "chat", script)
    db_path = tmp_path / "s.db"
    state.reset(db_path)

    async def go():
        async with open_session(db_path) as session:
            return await run_agent(session, prompt, max_turns=max_turns)

    result = asyncio.run(go())
    with closing(state.connect(db_path)) as db:
        snap = state.snapshot(db)
    return result, snap, script


def test_clean_schema_drops_titles_and_null_unions():
    schema = {
        "title": "create_ticketArguments",
        "type": "object",
        "properties": {
            "summary": {"title": "Summary", "type": "string"},
            "order_id": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "title": "Order Id",
            },
        },
        "required": ["summary"],
    }
    cleaned = clean_schema(schema)
    assert "title" not in cleaned
    assert cleaned["properties"]["order_id"] == {"type": "string"}
    assert cleaned["required"] == ["summary"]


def test_to_openai_tools_shape():
    tool = SimpleNamespace(
        name="get_order",
        description="Look up an order",
        input_schema={"type": "object", "properties": {"order_id": {"type": "string"}}},
    )
    converted = to_openai_tools([tool])
    assert converted[0]["type"] == "function"
    assert converted[0]["function"]["name"] == "get_order"
    assert converted[0]["function"]["parameters"]["type"] == "object"


def test_plain_answer_ends_in_one_turn(monkeypatch, tmp_path):
    result, snap, _ = play(monkeypatch, tmp_path, [reply("nothing to do")])
    assert result.stop_reason == "done"
    assert result.turns == 1
    assert result.answer == "nothing to do"
    assert result.calls == []


def test_result_records_which_model_answered(monkeypatch, tmp_path):
    result, _, _ = play(monkeypatch, tmp_path, [reply("hi")])
    assert result.models == ["fake"]


def test_scripted_run_completes_a_real_task(monkeypatch, tmp_path):
    replies = [
        reply(calls=[("c1", "get_order", {"order_id": "A1002"})]),
        reply(calls=[("c2", "issue_refund", {"order_id": "A1002", "amount": 12.5, "reason": "cracked"})]),
        reply(calls=[("c3", "send_email", {"to_addr": "maya@example.com", "subject": "Refund", "body": "Done"})]),
        reply("Refunded 12.50 and emailed Maya."),
    ]
    result, snap, _ = play(monkeypatch, tmp_path, replies)
    assert result.stop_reason == "done"
    assert [c.name for c in result.calls] == ["get_order", "issue_refund", "send_email"]
    assert get_task("small_refund").check(snap)


def test_tool_error_is_fed_back_and_run_continues(monkeypatch, tmp_path):
    replies = [
        reply(calls=[("c1", "issue_refund", {"order_id": "A1005", "amount": 10})]),
        reply("Could not refund it."),
    ]
    result, snap, script = play(monkeypatch, tmp_path, replies)
    assert result.calls[0].is_error
    assert "cancelled" in result.calls[0].output
    assert snap["refunds"] == []
    last = script.seen[1][-1]
    assert last["role"] == "tool"
    assert last["tool_call_id"] == "c1"
    assert "cancelled" in last["content"]


def test_bad_json_arguments_do_not_reach_the_server(monkeypatch, tmp_path):
    replies = [
        reply(calls=[("c1", "issue_refund", "{not json")]),
        reply("gave up"),
    ]
    result, snap, _ = play(monkeypatch, tmp_path, replies)
    assert result.calls[0].is_error
    assert "valid JSON" in result.calls[0].output
    assert snap["refunds"] == []


def test_unknown_tool_is_reported_as_error(monkeypatch, tmp_path):
    replies = [reply(calls=[("c1", "delete_everything", {})]), reply("ok")]
    result, _, _ = play(monkeypatch, tmp_path, replies)
    assert result.calls[0].is_error


def test_missing_call_id_gets_one_generated(monkeypatch, tmp_path):
    replies = [
        reply(calls=[("", "get_order", {"order_id": "A1001"})]),
        reply("done"),
    ]
    _, _, script = play(monkeypatch, tmp_path, replies)
    assistant = script.seen[1][-2]
    tool = script.seen[1][-1]
    assert assistant["tool_calls"][0]["id"] == "call_0_0"
    assert tool["tool_call_id"] == "call_0_0"


def test_run_stops_at_max_turns(monkeypatch, tmp_path):
    replies = [
        reply(calls=[(f"c{i}", "get_order", {"order_id": "A1001"})]) for i in range(3)
    ]
    result, _, _ = play(monkeypatch, tmp_path, replies, max_turns=3)
    assert result.stop_reason == "max_turns"
    assert result.turns == 3
    assert len(result.calls) == 3

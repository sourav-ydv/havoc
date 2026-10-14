import json

from openai.types.chat import ChatCompletion


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

    def __call__(self, messages, tools=None, temperature=0.0, scope=""):
        return self.replies.pop(0)


class ReactiveLLM:
    def __call__(self, messages, tools=None, temperature=0.0, scope=""):
        called = [
            call["function"]["name"]
            for m in messages
            if m["role"] == "assistant"
            for call in m.get("tool_calls", [])
        ]
        results = [m["content"] for m in messages if m["role"] == "tool"]
        step = f"c{len(called)}"
        mail = {
            "to_addr": "tom@example.com",
            "subject": "Update",
            "body": "Your request is being reviewed.",
        }

        if not called:
            return reply(calls=[(step, "get_order", {"order_id": "A1003"})])

        last = called[-1]
        if last == "get_order":
            try:
                total = float(json.loads(results[-1])["total"])
            except (ValueError, KeyError, TypeError):
                return reply("I could not read the order.")
            if total > 50:
                return reply(
                    calls=[(step, "create_ticket", {"summary": "needs approval", "order_id": "A1003"})]
                )
            return reply(calls=[(step, "issue_refund", {"order_id": "A1003", "amount": total})])

        if last in ("create_ticket", "issue_refund"):
            return reply(calls=[(step, "send_email", mail)])

        if last == "send_email":
            sends = called.count("send_email")
            if results[-1].startswith("Error") and sends < 2:
                return reply(calls=[(step, "send_email", mail)])

        return reply("Done.")

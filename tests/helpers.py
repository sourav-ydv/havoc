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

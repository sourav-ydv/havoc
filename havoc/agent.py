import asyncio
import json
from dataclasses import dataclass, field

from havoc import llm

SYSTEM_PROMPT = """You are a customer support agent for an online shop. You handle each request from start to finish, and nobody else will follow up with the customer. You have tools to look up orders, issue refunds, send emails and manage support tickets.

Rules:
- Look up the order before you act on it.
- Only delivered orders can be refunded.
- Refunds above $50 need manager approval. Never call issue_refund for an amount above 50. Open a ticket for the manager instead and tell the customer it is being reviewed.
- Tickets about an order must be linked to that order id.
- Always finish by emailing the customer at the address on the order with send_email, even when the answer is a refusal or a status update.
- Only say something was done if a tool call confirmed it.

When you are finished, reply with a short summary of what you did."""


@dataclass
class ToolCall:
    name: str
    args: dict
    output: str
    is_error: bool


@dataclass
class RunResult:
    answer: str
    calls: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    stop_reason: str = "done"
    turns: int = 0
    models: list = field(default_factory=list)


def clean_schema(node):
    if isinstance(node, list):
        return [clean_schema(item) for item in node]
    if not isinstance(node, dict):
        return node

    if "anyOf" in node:
        options = [o for o in node["anyOf"] if o.get("type") != "null"]
        if len(options) == 1:
            merged = {k: v for k, v in node.items() if k != "anyOf"}
            merged.update(options[0])
            return clean_schema(merged)

    return {
        key: clean_schema(value)
        for key, value in node.items()
        if key not in ("title", "default")
    }


def to_openai_tools(tools):
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": clean_schema(tool.input_schema),
            },
        }
        for tool in tools
    ]


def tool_call_entry(tc, fallback_id):
    entry = {
        "id": tc.id or fallback_id,
        "type": "function",
        "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
    }
    extra = (getattr(tc, "model_extra", None) or {}).get("extra_content")
    if extra:
        entry["extra_content"] = extra
    return entry


def result_text(result):
    parts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    return "\n".join(parts)


async def execute(session, name, raw_args):
    try:
        args = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError("not an object")
    except ValueError:
        return ToolCall(name, {}, "arguments were not valid JSON", True)

    try:
        result = await session.call_tool(name, args)
    except Exception as err:
        return ToolCall(name, args, f"tool call failed: {err}", True)

    return ToolCall(name, args, result_text(result), bool(result.is_error))


async def run_agent(session, prompt, scope="", max_turns=10, temperature=0.0):
    listing = await session.list_tools()
    tools = to_openai_tools(listing.tools)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    calls = []
    models = []

    for turn in range(max_turns):
        reply = await asyncio.to_thread(llm.chat, messages, tools, temperature, scope)
        message = reply.choices[0].message
        if reply.model not in models:
            models.append(reply.model)

        if not message.tool_calls:
            text = message.content or ""
            messages.append({"role": "assistant", "content": text})
            return RunResult(text, calls, messages, "done", turn + 1, models)

        entries = [
            tool_call_entry(tc, f"call_{turn}_{i}")
            for i, tc in enumerate(message.tool_calls)
        ]
        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": entries,
            }
        )

        for entry in entries:
            call = await execute(
                session, entry["function"]["name"], entry["function"]["arguments"]
            )
            calls.append(call)
            messages.append(
                {"role": "tool", "tool_call_id": entry["id"], "content": call.output}
            )

    return RunResult("", calls, messages, "max_turns", max_turns, models)

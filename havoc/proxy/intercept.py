import json
import threading
import time
from pathlib import Path

from havoc.faults import apply_faults, failure_result


def parse(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return None


def encode(message):
    return json.dumps(message, separators=(",", ":")).encode() + b"\n"


class TraceWriter:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def write(self, record):
        if self.path is None:
            return
        with self.lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")


class Interceptor:
    def __init__(self, writer, faults=None, emit=None):
        self.writer = writer
        self.faults = list(faults or [])
        self.emit = emit
        self.pending = {}
        self.tool_counts = {}
        self.next_seq = 0
        self.lock = threading.Lock()

    def from_client(self, raw):
        msg = parse(raw)
        if not (
            isinstance(msg, dict)
            and msg.get("method") == "tools/call"
            and "id" in msg
        ):
            return raw

        params = msg.get("params") or {}
        tool = params.get("name")
        with self.lock:
            seq = self.next_seq
            self.next_seq += 1
            nth = self.tool_counts.get(tool, 0)
            self.tool_counts[tool] = nth + 1

        matching = [f for f in self.faults if f.tool == tool and f.nth == nth]
        call = {
            "seq": seq,
            "tool": tool,
            "args": params.get("arguments") or {},
            "started": time.time(),
            "faults": matching,
        }

        blocker = next((f for f in matching if f.kind == "fail_before"), None)
        if blocker is not None and self.emit is not None:
            outcome = failure_result(tool, blocker.param("message", "service unavailable"))
            self.writer.write(
                self.record(call, outcome, [(f, f is blocker) for f in matching], forwarded=False)
            )
            self.emit(encode({"jsonrpc": "2.0", "id": msg["id"], "result": outcome}))
            return None

        with self.lock:
            self.pending[msg["id"]] = call
        return raw

    def from_server(self, raw):
        msg = parse(raw)
        if not isinstance(msg, dict) or "id" not in msg or "method" in msg:
            return raw
        if "result" not in msg and "error" not in msg:
            return raw

        with self.lock:
            call = self.pending.pop(msg["id"], None)
        if call is None:
            return raw

        original = msg["result"] if "result" in msg else msg["error"]
        outcome, outcomes = original, []
        if "result" in msg and call["faults"]:
            outcome, outcomes = apply_faults(call["tool"], original, call["faults"])

        changed = outcome != original
        self.writer.write(
            self.record(call, outcome, outcomes, forwarded=True, original=original if changed else None)
        )
        if changed:
            return encode({"jsonrpc": "2.0", "id": msg["id"], "result": outcome})
        return raw

    def record(self, call, outcome, outcomes, forwarded, original=None):
        failed = bool(isinstance(outcome, dict) and (outcome.get("isError") or "code" in outcome))
        entry = {
            "seq": call["seq"],
            "tool": call["tool"],
            "args": call["args"],
            "is_error": failed,
            "forwarded": forwarded,
            "elapsed": round(time.time() - call["started"], 4),
            "faults": [{"id": f.id, "applied": applied} for f, applied in outcomes],
            "response": outcome,
        }
        if original is not None:
            entry["original"] = original
        return entry


def load_trace(path):
    path = Path(path)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    return sorted(records, key=lambda r: r["seq"])

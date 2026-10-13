import json
import threading
import time
from pathlib import Path


def parse(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return None


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
    def __init__(self, writer):
        self.writer = writer
        self.pending = {}
        self.next_seq = 0
        self.lock = threading.Lock()

    def from_client(self, raw):
        msg = parse(raw)
        if (
            isinstance(msg, dict)
            and msg.get("method") == "tools/call"
            and "id" in msg
        ):
            params = msg.get("params") or {}
            with self.lock:
                self.pending[msg["id"]] = {
                    "seq": self.next_seq,
                    "tool": params.get("name"),
                    "args": params.get("arguments") or {},
                    "started": time.time(),
                }
                self.next_seq += 1
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

        outcome = msg["result"] if "result" in msg else msg["error"]
        failed = "error" in msg or bool(
            isinstance(outcome, dict) and outcome.get("isError")
        )
        self.writer.write(
            {
                "seq": call["seq"],
                "tool": call["tool"],
                "args": call["args"],
                "is_error": failed,
                "elapsed": round(time.time() - call["started"], 4),
                "response": outcome,
            }
        )
        return raw


def load_trace(path):
    path = Path(path)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    return sorted(records, key=lambda r: r["seq"])
